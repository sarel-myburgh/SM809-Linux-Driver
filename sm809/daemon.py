"""Background service for wheel-click + DPI+ profile switching.

With chord mode on, the wheel button sends F19 and DPI+ sends F24 (see
protocol.CHORD_CODES). This service reads them from the mouse's own HID
interface (no keyboard grab, no root) and turns them into:

- wheel, then DPI+ while it's held: next profile (no middle click is sent)
- wheel tapped: a middle click, through a virtual mouse
- wheel held past HOLD_DELAY: middle press until release (autoscroll, panning)
- DPI+ alone: DPI up
"""

from __future__ import annotations

import glob
import os
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import hidraw, profile
from . import protocol as p
from .uinput import BTN_MIDDLE, VirtualMouse

AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "sm809-daemon.desktop"
HOLD_DELAY = 0.3  # seconds the wheel must be held before it becomes a real middle press
RESCAN = 2.0  # seconds between looks for newly connected cable/receiver/Bluetooth


def log(msg: str):
    print(time.strftime("%T"), msg, flush=True)


def notify(msg: str):
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "SM809", "-i", "input-mouse", "-t", "2000", "SM809", msg])


def command_line() -> list[str]:
    appimage = os.environ.get("APPIMAGE")
    return [appimage] if appimage else [sys.executable, "-m", "sm809"]


def install_autostart():
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join(f'"{a}"' if " " in a else a for a in [*command_line(), "daemon"])
    AUTOSTART.write_text(
        "[Desktop Entry]\nType=Application\nName=SM809 profile switcher\n"
        f"Exec={exec_line}\nIcon=input-mouse\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n")


def remove_autostart():
    AUTOSTART.unlink(missing_ok=True)


def start_background():
    """Start the daemon now, detached, unless one is already running."""
    if running():
        return
    subprocess.Popen([*command_line(), "daemon"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def daemon_pids() -> list[int]:
    pids = []
    for cmd in glob.glob("/proc/[0-9]*/cmdline"):
        pid = int(cmd.split("/")[2])
        try:
            args = open(cmd, "rb").read().split(b"\0")
        except OSError:
            continue
        if pid != os.getpid() and b"daemon" in args and any(b"sm809" in a.lower() for a in args):
            pids.append(pid)
    return pids


def running() -> bool:
    return bool(daemon_pids())


def stop():
    for pid in daemon_pids():
        try:
            os.kill(pid, 15)
        except OSError:
            pass


class Link:
    """One connection to the mouse (cable, receiver or Bluetooth)."""

    def __init__(self, cand: hidraw.Candidate, vmouse: VirtualMouse):
        self.cand = cand
        self.dev = hidraw.Device(cand.path, cand.pid, cand.bus)
        self.vmouse = vmouse
        self.wheel = cand.pid not in p.NO_WHEEL_BINDING_PIDS
        self.bluetooth = cand.bus == 0x05
        st = self.dev.status() or {}
        self.stage = st.get("dpi_stage", 1) - 1
        self.sensor = st.get("sensor_type", 17)
        self.keys: set[int] = set()
        self.wheel_down_at: float | None = None  # wheel held, middle not sent yet
        self.middle_sent = False
        log(f"watching {cand.path} ({cand.mode})")

    def close(self):
        if self.middle_sent:
            self.vmouse.button(BTN_MIDDLE, False)
        self.dev.close()

    def deadline(self) -> float | None:
        return None if self.wheel_down_at is None else self.wheel_down_at + HOLD_DELAY

    def tick(self):
        """Wheel held long enough without DPI+: make it a real middle press."""
        if self.wheel_down_at is not None and time.monotonic() >= self.deadline():
            self.vmouse.button(BTN_MIDDLE, True)
            self.middle_sent, self.wheel_down_at = True, None

    def read(self):
        try:
            data = os.read(self.dev.fd, 64)
        except BlockingIOError:
            return
        if not data:
            raise OSError("device closed")
        if data[0] == p.STATUS_INPUT and len(data) > 2:
            self.stage = data[2]
        elif data[0] == 1:
            # Keyboard report: modifiers, then key codes (Bluetooth adds a reserved 0 byte).
            keys = set(data[2:]) - {0}
            for key in keys - self.keys:
                self.pressed(key)
            for key in self.keys - keys:
                self.released(key)
            self.keys = keys

    def pressed(self, key: int):
        if key == p.CHORD_MIDDLE_KEY:
            self.wheel_down_at = time.monotonic()
        elif key == p.SMART_DPI_KEY:
            chord = self.wheel_down_at is not None or self.middle_sent
            self.wheel_down_at = None  # a chord never becomes a middle click
            if self.bluetooth:
                notify("DPI+ and profile switching need the cable or the 2.4G receiver "
                       "(the mouse is read-only over Bluetooth)")
                return
            try:
                self.next_profile() if chord else self.dpi_up()
            except (p.ProtocolError, FileNotFoundError, hidraw.DeviceError) as e:
                log(f"error: {e}")
                notify(f"Error: {e}")

    def released(self, key: int):
        if key != p.CHORD_MIDDLE_KEY:
            return
        if self.wheel_down_at is not None:  # quick tap: click
            self.vmouse.button(BTN_MIDDLE, True)
            self.vmouse.button(BTN_MIDDLE, False)
        elif self.middle_sent:
            self.vmouse.button(BTN_MIDDLE, False)
        self.wheel_down_at, self.middle_sent = None, False

    def dpi_up(self):
        prof = profile.load(profile.active_name())
        stages = prof["dpi"]["stages"]
        new = min(self.stage + 1, len(stages) - 1)
        if new != self.stage:
            for pkt in p.dpi_packets(stages, new, self.sensor, self.dev.seq):
                self.dev.command(pkt)
            self.stage = new

    def next_profile(self):
        order = profile.cycle_order()
        cur = profile.active_name()
        name = order[(order.index(cur) + 1) % len(order)] if cur in order else order[0]
        prof = profile.load(name)
        for _, packets in profile.compile_sections(prof, self.dev.seq, self.sensor,
                                                   wheel=self.wheel, chord=True):
            for pkt in packets:
                self.dev.command(pkt)
        profile.set_active(name)
        self.stage = prof["dpi"]["current"] - 1
        log(f"profile → {name}")
        notify(f"Profile: {name}")


def run():
    """Listen on every connection at once: the mouse can be on the cable, the
    receiver or Bluetooth, and the receiver stays present (silent) while the
    mouse is in another mode."""
    log("sm809 daemon: wheel-click + DPI+ switches profiles")
    vmouse = VirtualMouse()
    links: dict[str, Link] = {}
    next_scan = 0.0
    while True:
        now = time.monotonic()
        if now >= next_scan:
            found = {c.path: c for c in hidraw.find_devices()}
            for path in set(links) - set(found):
                log(f"lost {path}")
                links.pop(path).close()
            for path, cand in found.items():
                if path not in links:
                    try:
                        links[path] = Link(cand, vmouse)
                    except (OSError, hidraw.DeviceError) as e:
                        log(f"can't open {path}: {e}")
            next_scan = now + RESCAN
        deadlines = [d for d in (l.deadline() for l in links.values()) if d is not None]
        timeout = max(0.0, min([next_scan, *deadlines]) - time.monotonic())
        ready = select.select([l.dev.fd for l in links.values()], [], [], timeout)[0]
        for path, link in list(links.items()):
            try:
                if link.dev.fd in ready:
                    link.read()
                link.tick()
            except OSError as e:
                log(f"lost {path}: {e}")
                links.pop(path).close()

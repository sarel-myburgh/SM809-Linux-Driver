"""Linux hidraw transport. Works the same for USB, the 2.4G receiver and Bluetooth."""

from __future__ import annotations

import fcntl
import glob
import os
import random
import select
import time
from dataclasses import dataclass
from pathlib import Path

from . import protocol as p

SEQ_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"sm809-seq-{os.getuid()}"


def _ioc_rw(nr: int, size: int) -> int:
    # _IOC(_IOC_READ | _IOC_WRITE, 'H', nr, size)
    return (3 << 30) | (size << 16) | (ord("H") << 8) | nr


def HIDIOCSFEATURE(size: int) -> int:
    return _ioc_rw(0x06, size)


def HIDIOCGFEATURE(size: int) -> int:
    return _ioc_rw(0x07, size)


@dataclass
class Candidate:
    path: str
    bus: int
    vid: int
    pid: int
    name: str

    @property
    def mode(self) -> str:
        if self.bus == 0x05:
            return "Bluetooth"
        return p.PRODUCT_IDS.get(self.pid, "unknown")


def feature_report_ids(desc: bytes) -> set[int]:
    """Report IDs that carry a Feature main item in a HID report descriptor."""
    ids, rid, i = set(), 0, 0
    while i < len(desc):
        b = desc[i]
        if b == 0xFE:  # long item
            i += 3 + desc[i + 1]
            continue
        size = (0, 1, 2, 4)[b & 3]
        tag = b & 0xFC
        val = int.from_bytes(desc[i + 1:i + 1 + size], "little")
        if tag == 0x84:
            rid = val
        elif tag == 0xB0:
            ids.add(rid)
        i += 1 + size
    return ids


def find_devices() -> list[Candidate]:
    found = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            uevent = dict(
                line.split("=", 1)
                for line in open(f"{node}/device/uevent").read().splitlines()
                if "=" in line
            )
            bus, vid, pid = (int(x, 16) for x in uevent["HID_ID"].split(":"))
            desc = open(f"{node}/device/report_descriptor", "rb").read()
        except (OSError, KeyError, ValueError):
            continue
        # The 026E revision's Bluetooth PID isn't known, so accept any Elan
        # device on the Bluetooth bus; the feature-6 check below narrows it.
        if vid != p.VENDOR_ID or (pid not in p.PRODUCT_IDS and bus != 0x05):
            continue
        if p.CMD_REPORT not in feature_report_ids(desc):
            continue  # the plain mouse interface; we want the vendor one
        found.append(Candidate(f"/dev/{os.path.basename(node)}", bus, vid, pid,
                               uevent.get("HID_NAME", "")))
    return found


def best_device() -> Candidate | None:
    """The connection to talk to: cable, then receiver, then (read-only) Bluetooth."""
    found = find_devices()
    found.sort(key=lambda c: {"USB": 0, "2.4G": 1, "Bluetooth": 2}.get(c.mode, 3))
    return found[0] if found else None


class DeviceError(RuntimeError):
    pass


class Device:
    def __init__(self, path: str, pid: int | None = None, bus: int | None = None):
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as e:
            raise DeviceError(
                f"no permission to open {path}. Install the udev rule: "
                "sudo ./install-udev.sh, then replug the mouse/receiver"
            ) from e
        self.path = path
        self.pid = pid
        self.bus = bus
        # The firmware silently drops a command whose seq equals the last one it
        # accepted, so carry the counter across runs instead of restarting at 1.
        try:
            self._seq = int(SEQ_FILE.read_text())
        except (OSError, ValueError):
            self._seq = random.randrange(256)

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def seq(self) -> int:
        self._seq = (self._seq + 1) % 256
        try:
            SEQ_FILE.write_text(str(self._seq))
        except OSError:
            pass
        return self._seq

    def set_feature(self, report_id: int, payload: bytes):
        buf = bytearray([report_id]) + payload
        fcntl.ioctl(self.fd, HIDIOCSFEATURE(len(buf)), buf)

    def get_feature(self, report_id: int, length: int = p.REPORT_LEN) -> bytes:
        buf = bytearray(1 + length)
        buf[0] = report_id
        n = fcntl.ioctl(self.fd, HIDIOCGFEATURE(len(buf)), buf)
        return bytes(buf[:n if isinstance(n, int) and n > 0 else len(buf)])

    def command(self, packet: bytes, retries: int = 3) -> None:
        """Send one command and wait for the ack (feature 6, byte 1 == 1).

        The ack only proves the transfer happened: firmware 0109 answers 1 even
        for a bad checksum or a dropped command. Retries reuse the same seq, so
        a duplicate is ignored by the mouse.
        """
        for attempt in range(retries):
            time.sleep(0.02 + attempt * 0.05)
            try:
                self.set_feature(p.CMD_REPORT, packet)
            except OSError as e:
                if self.bus == 0x05:
                    raise DeviceError(
                        "the mouse refuses settings changes over Bluetooth (reading works). "
                        "Connect the cable or the 2.4G receiver; settings are stored on the "
                        "mouse and also apply in Bluetooth mode") from e
                last = e
                continue
            time.sleep(0.06)
            try:
                ack = self.get_feature(p.CMD_REPORT)
            except OSError as e:
                last = e
                continue
            if len(ack) > 1 and ack[1] == 1:
                return
            last = DeviceError(f"device did not ack (got {ack[:4].hex(' ')})")
        raise DeviceError(f"command {packet[0]:#04x} failed: {last}")

    def status(self, timeout: float = 4.0) -> dict | None:
        """Ask for and read the status input report (ID 4). Bluetooth can take ~2 s."""
        self.get_feature(p.STATUS_FEATURE)
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            if not select.select([self.fd], [], [], left)[0]:
                break
            try:
                data = os.read(self.fd, 64)
            except BlockingIOError:
                continue
            if data and data[0] == p.STATUS_INPUT and len(data) >= 10:
                return p.parse_status(data[1:])
        return None

"""sm809: configure the Solakaka SM809 Pro mouse on Linux."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from . import hidraw, profile
from . import protocol as p


class DryRun:
    """Stands in for a Device: prints packets instead of sending them."""

    path = "(dry run)"
    pid = None

    def __init__(self):
        self._seq = 0

    def seq(self):
        self._seq = (self._seq + 1) % 256
        return self._seq

    def command(self, packet: bytes):
        print("  06 " + packet.hex(" "))

    def status(self):
        return None

    def close(self):
        pass


def open_device(args):
    found = hidraw.find_devices()
    # Prefer a direct link over the receiver when several are present.
    # Bluetooth last: it can read status but the mouse refuses writes over it.
    rank = {"USB": 0, "2.4G": 1, "Bluetooth": 2}
    found.sort(key=lambda c: rank.get(c.mode, 3))
    if args.device:
        found = [c for c in found if c.path == args.device] or [
            hidraw.Candidate(args.device, 0, p.VENDOR_ID, 0, "")]
    if args.dry_run:
        dry = DryRun()
        dry.pid = found[0].pid if found else None  # dry runs match the connected revision
        return dry
    if not found:
        sys.exit("No SM809 Pro found. Check `sm809 devices`; wired, 2.4G receiver and "
                 "Bluetooth are all supported once connected.")
    return hidraw.Device(found[0].path, found[0].pid or None, found[0].bus or None)


def sensor_of(dev) -> int:
    st = dev.status()
    return st["sensor_type"] if st and st["sensor_type"] in p.SENSORS else 17


def push(dev, sections):
    for name, packets in sections:
        print(f"{name}:" if isinstance(dev, DryRun) else f"→ {name}", flush=True)
        for pkt in packets:
            dev.command(pkt)


def apply_profile(args, prof, only=None):
    dev = open_device(args)
    try:
        sensor = sensor_of(dev) if "dpi" in (only or profile.SECTIONS) else 17
        wheel = dev.pid not in p.NO_WHEEL_BINDING_PIDS
        chord = profile.settings()["chord"]
        push(dev, profile.compile_sections(prof, dev.seq, sensor, only, wheel, chord))
    finally:
        dev.close()
    if not args.dry_run:
        print(f"Applied to {dev.path}")


def edit_and_apply(args, section, changes):
    name = args.profile or profile.active_name()
    prof = profile.load(name)
    prof[section].update(changes)
    profile.compile_sections(prof, lambda: 0, only=[section])  # validate before saving
    if not args.no_apply:
        apply_profile(args, prof, [section])
    if not args.dry_run:
        profile.save(name, prof)
        print(f"Saved to profile '{name}'")


# ---------------------------------------------------------------- commands

def cmd_devices(args):
    found = hidraw.find_devices()
    if not found:
        print("No SM809 Pro vendor interface found.")
        return
    for c in found:
        access = "ok" if os.access(c.path, os.R_OK | os.W_OK) else "no access (install udev rule)"
        print(f"{c.path}  {c.vid:04x}:{c.pid:04x}  {c.mode:<22} {c.name}  [{access}]")


def cmd_status(args):
    dev = open_device(args)
    try:
        st = dev.status()
    finally:
        dev.close()
    if not st:
        sys.exit("No status report received.")
    width = max(map(len, st))
    for k, v in st.items():
        print(f"{k:<{width}}  {v}")


def cmd_apply(args):
    name = args.name or profile.active_name()
    prof = profile.load(name)
    only = args.only.split(",") if args.only else None
    apply_profile(args, prof, only)
    if args.name and not args.dry_run and "/" not in args.name:
        profile.set_active(args.name)


def cmd_bind(args):
    b = p.button_id(args.button)
    key = p.BUTTON_NAMES[b]
    name = args.profile or profile.active_name()
    prof = profile.load(name)
    for k in list(prof["buttons"]):  # drop aliases ("12" vs "left") of the same button
        if p.button_id(k) == b:
            del prof["buttons"][k]
    prof["buttons"][key] = args.action
    profile.button_packets(prof, lambda: 0)
    if not args.no_apply:
        apply_profile(args, prof, ["buttons"])
    if not args.dry_run:
        profile.save(name, prof)
        print(f"{key} → {args.action}  (profile '{name}')")


def cmd_dpi(args):
    changes = {"stages": args.stages}
    if args.current:
        changes["current"] = args.current
    else:
        changes["current"] = min(profile.load(args.profile)["dpi"]["current"], len(args.stages))
    edit_and_apply(args, "dpi", changes)


def cmd_rate(args):
    edit_and_apply(args, "performance", {"polling_rate": args.hz})


def cmd_lighting(args):
    changes = {"mode": args.mode}
    for k in ("color", "brightness", "speed"):
        if getattr(args, k) is not None:
            changes[k] = getattr(args, k)
    edit_and_apply(args, "lighting", changes)


def cmd_profile(args):
    if args.action == "list":
        active = profile.active_name()
        for n in profile.list_profiles():
            print(("* " if n == active else "  ") + n)
        return
    if not args.name:
        sys.exit(f"profile {args.action} needs a name")
    path = profile.profile_path(args.name)
    if args.action == "new":
        if path.exists():
            sys.exit(f"{path} already exists")
        profile.ensure_default()
        if args.source:
            path.write_text(profile.profile_path(args.source).read_text())
        else:
            path.write_text(profile.default_text())
        print(f"Created {path}")
    elif args.action == "show":
        print(path.read_text())
    elif args.action == "path":
        print(path)
    elif args.action == "use":
        prof = profile.load(args.name)
        apply_profile(args, prof)
        if not args.dry_run:
            profile.set_active(args.name)
    elif args.action == "edit":
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or shutil.which("nano") or "vi"
        profile.load(args.name)
        subprocess.call([editor, str(path)])
        profile.compile_sections(profile.load(args.name), lambda: 0)
        print(f"Profile OK. Apply with: sm809 apply {args.name}")
    elif args.action == "delete":
        if args.name == "default":
            sys.exit("refusing to delete the default profile")
        path.unlink()
        print(f"Deleted {path}")


def cmd_gui(args):
    try:
        from . import gui
    except ImportError as e:
        sys.exit(f"The GUI needs PyQt6 (sudo apt install python3-pyqt6): {e}")
    gui.main()


def cmd_daemon(args):
    from . import daemon
    daemon.run()


def cmd_chord(args):
    from . import daemon
    s = profile.settings()
    s["chord"] = args.state == "on"
    if args.cycle:
        s["cycle"] = args.cycle.split(",")
    if args.dry_run:
        print(s)
        return
    profile.save_settings(s)
    apply_profile(args, profile.load(), ["buttons"])  # DPI+ → F24 or back to DPI+
    if s["chord"]:
        daemon.install_autostart()
        daemon.start_background()
        print(f"Wheel-click + DPI+ now cycles: {', '.join(profile.cycle_order())}")
        print(f"Daemon started and added to autostart ({daemon.AUTOSTART})")
    else:
        daemon.remove_autostart()
        daemon.stop()
        print("Profile switching off; the wheel and DPI+ are plain buttons again.")


def cmd_reset(args):
    if not args.yes:
        sys.exit("This restores the mouse's factory settings. Re-run with --yes.")
    dev = open_device(args)
    try:
        push(dev, [("reset", [p.reset_packet(dev.seq)])])
    finally:
        dev.close()


def cmd_raw(args):
    data = bytes.fromhex(args.hex)
    dev = open_device(args)
    if args.frame:
        pkt = p.frame([data[0], dev.seq(), *data[1:]], int(args.pad, 16))
    else:
        pkt = data.ljust(p.REPORT_LEN, b"\0")[:p.REPORT_LEN]
    try:
        push(dev, [("raw", [pkt])])
    finally:
        dev.close()


ACTIONS_HELP = """\
Buttons (see docs/buttons.png):
  side1 … side9   the 9-key side panel        edge-front, edge-back  beside left click
  left right middle  dpi-up dpi-down  wheel-up wheel-down   (or numbers 1-18)

Actions:
  Mouse      left right middle back forward wheel-up wheel-down
  Keys       a, f5, f13-f24, enter, space, shift, ctrl, alt, win, printscreen …
  Combos     ctrl+c, ctrl+shift+c, ctrl+backspace, win+d, alt+tab …   (held while the button is held)
  Shortcuts  lock show-desktop screenshot copy paste cut undo redo select-all save find
             new-tab close-tab reopen-tab next-tab prev-tab close-window switch-window delete-word
  System     power sleep screensaver brightness-up brightness-down file-manager calculator email
             browser browser-search browser-home browser-back browser-forward browser-refresh
  Media      play-pause next-track prev-track stop mute volume-up volume-down player
             consumer:0xNNNN (any HID consumer usage)
  Text       text:anything you want typed   (US layout)
  DPI        dpi-cycle dpi-up dpi-down dpi-lock:200 … dpi-lock:1200
  Fire       triple-fire   fire:INTERVAL:COUNT (interval 5-255, count 0 = while held)
  Other      disable lighting-toggle macro:NAME raw:XX,YY,ZZ

Profile switching: `sm809 chord on` makes wheel-click + DPI+ cycle profiles
(DPI+ alone still steps DPI up). It needs `sm809 daemon` running; `chord on`
adds it to your desktop autostart.

Macro steps ([macros.NAME] steps = [...], mode = once | repeat | hold | toggle):
  tap:KEY  down:KEY  up:KEY  (append @MS to wait after)   delay:MS   move:left:N
  KEY can also be ctrl/shift/alt/win or mouse-left/right/middle/forward/back
"""


def main(argv=None):
    # Global flags are accepted before or after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--device", default=argparse.SUPPRESS,
                        help="hidraw node to use (default: auto-detect)")
    common.add_argument("-n", "--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="print packets instead of sending")
    ap = argparse.ArgumentParser(prog="sm809", description=__doc__, parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser
    sub.add_parser = lambda *a, **k: add(*a, parents=[common], **k)

    sub.add_parser("devices", help="list connected SM809 interfaces").set_defaults(fn=cmd_devices)
    sub.add_parser("status", help="show DPI stage, polling rate, battery, sensor").set_defaults(fn=cmd_status)
    sub.add_parser("actions", help="list button names and actions").set_defaults(
        fn=lambda a: print(ACTIONS_HELP, end=""))

    s = sub.add_parser("apply", help="push a profile to the mouse")
    s.add_argument("name", nargs="?", help="profile name or path (default: active)")
    s.add_argument("--only", help=f"comma-separated subset of {','.join(profile.SECTIONS)}")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("profile", help="manage profiles")
    s.add_argument("action", choices=["list", "new", "show", "edit", "use", "delete", "path"])
    s.add_argument("name", nargs="?")
    s.add_argument("--from", dest="source", help="copy from this profile (with new)")
    s.set_defaults(fn=cmd_profile)

    def editing(s):
        s.add_argument("--profile", help="profile to edit (default: active)")
        s.add_argument("--no-apply", action="store_true", help="only save to the profile")

    s = sub.add_parser("bind", help="assign an action to a button")
    s.add_argument("button")
    s.add_argument("action")
    editing(s)
    s.set_defaults(fn=cmd_bind)

    s = sub.add_parser("dpi", help="set DPI stages")
    s.add_argument("stages", nargs="+", type=int)
    s.add_argument("--current", type=int, help="1-based stage to activate")
    editing(s)
    s.set_defaults(fn=cmd_dpi)

    s = sub.add_parser("rate", help="set polling rate")
    s.add_argument("hz", type=int, choices=sorted(p.RATES))
    editing(s)
    s.set_defaults(fn=cmd_rate)

    s = sub.add_parser("lighting", help="set lighting effect")
    s.add_argument("mode", choices=list(p.LIGHT_MODES))
    s.add_argument("--color")
    s.add_argument("--brightness", type=int, choices=range(1, 5))
    s.add_argument("--speed", type=int, choices=range(1, 4))
    editing(s)
    s.set_defaults(fn=cmd_lighting)

    sub.add_parser("daemon", help="run the wheel+DPI+ profile switcher (foreground)").set_defaults(
        fn=cmd_daemon)
    sub.add_parser("gui", help="open the graphical configurator").set_defaults(fn=cmd_gui)

    s = sub.add_parser("chord", help="turn wheel-click + DPI+ profile switching on or off")
    s.add_argument("state", choices=["on", "off"])
    s.add_argument("--cycle", help="comma-separated profile order (default: all, alphabetical)")
    s.set_defaults(fn=cmd_chord)

    s = sub.add_parser("reset", help="factory-reset the mouse")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_reset)

    s = sub.add_parser("raw", help="send a raw feature-6 command (for protocol work)")
    s.add_argument("hex", help="with --frame: CMD SUB ARGS… (seq/checksum added); else all 31 bytes")
    s.add_argument("--frame", action="store_true")
    s.add_argument("--pad", default="ff", help="pad byte with --frame (hex, default ff)")
    s.set_defaults(fn=cmd_raw)

    args = ap.parse_args(argv)
    args.device = getattr(args, "device", None)
    args.dry_run = getattr(args, "dry_run", False)
    try:
        args.fn(args)
    except (p.ProtocolError, hidraw.DeviceError, FileNotFoundError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()

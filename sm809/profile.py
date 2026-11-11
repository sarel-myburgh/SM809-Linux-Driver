"""Host-side profiles.

The SM809 has no on-board profile slots (the vendor tool keeps its profiles in
browser storage too), so a profile is a TOML file that gets pushed to the
mouse in full or by section.
"""

from __future__ import annotations

import os
import tomllib
from importlib import resources
from pathlib import Path

from . import protocol as p

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sm809"
PROFILE_DIR = CONFIG_DIR / "profiles"
ACTIVE_FILE = CONFIG_DIR / "active"
SETTINGS_FILE = CONFIG_DIR / "settings.toml"
DEFAULT_SETTINGS = {"chord": False, "cycle": []}

SECTIONS = ("dpi", "performance", "power", "lighting", "buttons")


def default_text() -> str:
    return resources.files(__package__).joinpath("default.toml").read_text()


def ensure_default():
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    path = PROFILE_DIR / "default.toml"
    if not path.exists():
        path.write_text(default_text())


def profile_path(name: str) -> Path:
    if "/" in name or name.endswith(".toml"):
        return Path(name)
    return PROFILE_DIR / f"{name}.toml"


def list_profiles() -> list[str]:
    ensure_default()
    return sorted(f.stem for f in PROFILE_DIR.glob("*.toml"))


def active_name() -> str:
    try:
        return ACTIVE_FILE.read_text().strip() or "default"
    except FileNotFoundError:
        return "default"


def set_active(name: str):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(name + "\n")


def settings() -> dict:
    """App settings: chord (wheel+DPI+ switches profiles), cycle (profile order)."""
    try:
        with open(SETTINGS_FILE, "rb") as f:
            return {**DEFAULT_SETTINGS, **tomllib.load(f)}
    except FileNotFoundError:
        return dict(DEFAULT_SETTINGS)


def save_settings(s: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text("".join(f"{k} = {_val(v)}\n" for k, v in s.items()))


def cycle_order() -> list[str]:
    names = list_profiles()
    order = [n for n in settings()["cycle"] if n in names]
    return order or names


def load(name: str | None = None) -> dict:
    ensure_default()
    path = profile_path(name or active_name())
    if not path.exists():
        raise FileNotFoundError(f"no profile {path}; see `sm809 profile list`")
    with open(path, "rb") as f:
        prof = tomllib.load(f)
    base = tomllib.loads(default_text())
    for key, val in base.items():  # fill sections the user left out
        if isinstance(val, dict):
            prof[key] = {**val, **prof.get(key, {})}
    prof.setdefault("macros", {})
    return prof


def save(name: str, prof: dict):
    path = profile_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(prof))


def compile_sections(prof: dict, seq, sensor: int = 17, only=None,
                     wheel: bool = True, chord: bool = False) -> list[tuple[str, list[bytes]]]:
    """Turn a profile into (section, packets) in send order.

    wheel=False for revisions whose wheel up/down can't be remapped.
    chord=True makes DPI+ send F24 for `sm809 daemon` (see protocol.SMART_DPI_KEY).
    """
    want = set(only or SECTIONS)
    out = []
    if "dpi" in want:
        d = prof["dpi"]
        out.append(("dpi", p.dpi_packets(d["stages"], d["current"] - 1, sensor, seq)
                    + [p.dpi_colors_packet(d.get("colors", []), seq)]))
    if "performance" in want:
        perf = prof["performance"]
        out.append(("performance", [
            p.rate_packet(perf["polling_rate"], seq),
            p.lod_packet(perf["lift_off_mm"], seq),
            p.sensor_packet(perf["angle_snap"], perf["motion_sync"], perf["ripple_control"], seq),
        ]))
    if "power" in want:
        pw = prof["power"]
        out.append(("power", [p.power_packet(pw["sleep"], pw["motion_wake"], pw["motion_lighting"], seq)]))
    if "lighting" in want:
        li = prof["lighting"]
        out.append(("lighting", [p.lighting_packet(
            li["mode"], li.get("color", "#ffffff"), li.get("brightness"), li.get("speed"), seq)]))
    if "buttons" in want:
        out.append(("buttons", button_packets(prof, seq, wheel, chord)))
    return out


def button_packets(prof: dict, seq, wheel: bool = True, chord: bool = False) -> list[bytes]:
    macros = dict(prof.get("macros", {}))
    buttons = {}
    for key, action in prof["buttons"].items():
        # "text:..." types a string: compile it to a hidden per-button macro.
        if action.startswith("text:"):
            name = f"_text_{p.button_id(key)}"
            macros[name] = {"mode": "once", "steps": p.text_steps(action[5:])}
            action = f"macro:{name}"
        buttons[key] = action

    used = [a.strip()[6:] for a in buttons.values() if a.strip().lower().startswith("macro:")]
    data = {}
    for name in dict.fromkeys(used):
        if name not in macros:
            raise p.ProtocolError(f"macro {name!r} is not defined in this profile")
        data[name] = p.macro_events(macros[name].get("steps", []))
    blocks = p.allocate_macro_blocks({n: len(d) for n, d in data.items()})

    modes = {}
    for name, m in macros.items():
        mode = m.get("mode", "once")
        if mode not in p.MACRO_MODES:
            raise p.ProtocolError(f"macro {name}: mode must be one of {', '.join(p.MACRO_MODES)}")
        modes[name] = (mode, max(1, min(int(m.get("repeat", 1)), 255)), blocks.get(name, 0))

    codes = {}
    for key, action in buttons.items():
        b = p.button_id(key)
        if not wheel and b in (17, 18):
            if action.strip().lower() != p.DEFAULT_BUTTONS[b]:
                raise p.ProtocolError(f"{p.BUTTON_NAMES[b]} can't be remapped on this mouse revision")
            continue  # slot left at FF, as the vendor tool does
        codes[b] = p.action_code(action, b, modes)
    if chord:
        codes.update({b: list(c) for b, c in p.CHORD_CODES.items()})
    if [0x10, 1, 0] not in codes.values():  # checked after chord: middle can't be the only left
        raise p.ProtocolError("at least one button must stay mapped to 'left'")

    packets = p.buttons_packets(codes, seq)
    if data:
        packets.append(p.macro_erase_packet(seq))
        for name, events in data.items():
            packets += p.macro_packets(blocks[name], events, seq)
    return packets


# Minimal TOML writer for the profile schema (tables of scalars/lists).

def _val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _key(k: str) -> str:
    return k if k.replace("-", "").replace("_", "").isalnum() else _val(k)


def dump(prof: dict) -> str:
    lines = []
    for section in (*SECTIONS, *(k for k in prof if k not in SECTIONS and k != "macros")):
        if section not in prof:
            continue
        lines.append(f"[{section}]")
        lines += [f"{_key(k)} = {_val(v)}" for k, v in prof[section].items()]
        lines.append("")
    for name, m in prof.get("macros", {}).items():
        lines.append(f"[macros.{_key(name)}]")
        lines += [f"{_key(k)} = {_val(v)}" for k, v in m.items()]
        lines.append("")
    return "\n".join(lines)

"""SM809 Pro wire protocol.

Reverse-engineered from the vendor WebHID configurator
(driver.yuandaxin-tech.com/HYX/V1000_SM809_3311_Full). See docs/PROTOCOL.md.

Everything here is pure: functions build the 31-byte payloads of HID feature
report 6. Nothing in this module touches a device.
"""

from __future__ import annotations

import re

VENDOR_ID = 0x04F3
# Two hardware revisions; both speak the same protocol. The 026E/026F IDs come
# from the V1000_SM809_3311 web driver and the 2025-08-11 wired firmware; 026D
# was seen as "SM809 BT1" over Bluetooth LE.
PRODUCT_IDS = {
    0x026E: "USB",
    0x026F: "2.4G",
    0x809E: "USB",
    0x809F: "2.4G",
    0x809D: "Bluetooth",
    0x026D: "Bluetooth",
}
# On this revision the vendor tool maps only buttons 1-16 and leaves the
# wheel-up/down slots at FF.
NO_WHEEL_BINDING_PIDS = {0x026D, 0x026E, 0x026F}

CMD_REPORT = 6  # feature report: commands out, ack (byte 1 == 1) back
STATUS_FEATURE = 5  # feature report: reading it prompts a status report
STATUS_INPUT = 4  # input report: link, DPI stage, rate, battery, sensor
REPORT_LEN = 31  # payload bytes of reports 4/5/6, excluding report ID

CMD_BUTTONS = 0x01
CMD_DPI = 0x02
CMD_DPI_COLORS = 0x03
CMD_RATE = 0x04
CMD_LOD = 0x05
CMD_LIGHTING = 0x06
CMD_POWER = 0x07
CMD_SENSOR = 0x08
CMD_RESET = 0x0F
CMD_MACRO_DATA = 0x10
CMD_MACRO_ERASE = 0x11


class ProtocolError(ValueError):
    pass


def frame(body: list[int], pad: int) -> bytes:
    """Pad a command to 30 bytes and insert the checksum at offset 3.

    Checksum = (report ID 6 + sum of the other bytes) & 0xFF.
    """
    body = list(body)
    while len(body) < 30:
        body.append(pad)
    body.insert(3, (CMD_REPORT + sum(body)) & 0xFF)
    return bytes(b & 0xFF for b in body[:REPORT_LEN])


# --------------------------------------------------------------------------
# Buttons

# Numbering matches docs/buttons.png and the vendor UI.
BUTTONS = {
    "side1": 1, "side2": 2, "side3": 3, "side4": 4, "side5": 5,
    "side6": 6, "side7": 7, "side8": 8, "side9": 9,
    "edge-front": 10, "edge-back": 11,
    "left": 12, "right": 13, "middle": 14,
    "dpi-up": 15, "dpi-down": 16,
    "wheel-up": 17, "wheel-down": 18,
}
BUTTON_NAMES = {v: k for k, v in BUTTONS.items()}

# Firmware slot order: (packet sub-command, button IDs in slot order).
BUTTON_PACKETS = [
    (0x02, [12, 13, 14, 15, 16, 10, 11, 1, 2]),
    (0x12, [3, 4, 5, 6, 9, 7, 8, 17, 18]),
]

DEFAULT_BUTTONS = {
    1: "win+d", 2: "2", 3: "1", 4: "forward", 5: "4", 6: "3", 7: "6",
    8: "5", 9: "back", 10: "fire:16:2", 11: "my-computer", 12: "left",
    13: "right", 14: "middle", 15: "dpi-up", 16: "dpi-down",
    17: "wheel-up", 18: "wheel-down",
}


def button_id(name: str | int) -> int:
    if isinstance(name, int) or str(name).isdigit():
        n = int(name)
        if n in BUTTON_NAMES:
            return n
    elif name.lower() in BUTTONS:
        return BUTTONS[name.lower()]
    raise ProtocolError(f"unknown button {name!r}; use 1-18 or one of {', '.join(BUTTONS)}")


# HID keyboard usage IDs (USB HID Usage Tables, page 0x07).
KEYS: dict[str, int] = {chr(ord("a") + i): 4 + i for i in range(26)}
KEYS.update({str(i): 30 + (i - 1) for i in range(1, 10)})
KEYS.update({f"f{i}": 57 + i for i in range(1, 13)})
KEYS.update({f"f{i}": 104 + i - 13 for i in range(13, 25)})
KEYS.update({
    "0": 39, "enter": 40, "esc": 41, "escape": 41, "backspace": 42,
    "tab": 43, "space": 44, "minus": 45, "-": 45, "equal": 46, "=": 46,
    "[": 47, "]": 48, "\\": 49, ";": 51, "'": 52, "`": 53, ",": 54,
    ".": 55, "/": 56, "capslock": 57, "printscreen": 70, "scrolllock": 71,
    "pause": 72, "insert": 73, "home": 74, "pageup": 75, "delete": 76,
    "end": 77, "pagedown": 78, "right": 79, "left": 80, "down": 81,
    "up": 82, "arrow-right": 79, "arrow-left": 80, "arrow-down": 81,
    "arrow-up": 82, "numlock": 83, "kp/": 84, "kp*": 85, "kp-": 86, "kp+": 87,
    "kpenter": 88, "kp1": 89, "kp2": 90, "kp3": 91, "kp4": 92, "kp5": 93,
    "kp6": 94, "kp7": 95, "kp8": 96, "kp9": 97, "kp0": 98, "kp.": 99,
    "menu": 101, "mute": 127, "volumeup": 128, "volumedown": 129,
    "return": 40, "del": 76, "ins": 73, "pgup": 75, "pgdn": 78,
    "grave": 53, "apostrophe": 52, "semicolon": 51, "comma": 54,
    "period": 55, "slash": 56, "backslash": 49, "leftbrace": 47, "rightbrace": 48,
})
# Left modifiers are confirmed on hardware; the right-hand bits follow the
# standard HID modifier byte and are untested.
MODIFIERS = {"ctrl": 1, "control": 1, "shift": 2, "alt": 4, "win": 8, "super": 8, "meta": 8,
             "rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "altgr": 0x40, "rwin": 0x80}

MOUSE_MASK = {"left": 1, "right": 2, "middle": 4, "back": 8, "forward": 16,
              "wheel-up": 32, "wheel-down": 64}

# HID consumer usages (page 0x0C), as Linux maps them to keys.
CONSUMER = {
    "player": 0x183, "prev-track": 0xB6, "next-track": 0xB5, "stop": 0xB7,
    "play-pause": 0xCD, "mute": 0xE2, "volume-up": 0xE9, "volume-down": 0xEA,
    "email": 0x18A, "calculator": 0x192, "my-computer": 0x194, "file-manager": 0x194,
    "power": 0x30, "sleep": 0x32, "screensaver": 0x19E,
    "brightness-up": 0x6F, "brightness-down": 0x70,
    "browser": 0x196, "browser-search": 0x221, "browser-home": 0x223,
    "browser-back": 0x224, "browser-forward": 0x225, "browser-refresh": 0x227,
}

# Named shortcuts: friendly names for common key combos.
SHORTCUTS = {
    "lock": "win+l", "show-desktop": "win+d", "screenshot": "printscreen",
    "copy": "ctrl+c", "paste": "ctrl+v", "cut": "ctrl+x", "undo": "ctrl+z",
    "redo": "ctrl+shift+z", "select-all": "ctrl+a", "save": "ctrl+s", "find": "ctrl+f",
    "new-tab": "ctrl+t", "close-tab": "ctrl+w", "reopen-tab": "ctrl+shift+t",
    "next-tab": "ctrl+tab", "prev-tab": "ctrl+shift+tab", "close-window": "alt+f4",
    "switch-window": "alt+tab", "delete-word": "ctrl+backspace",
}

# With wheel+DPI+ profile switching on, the wheel button sends F19 and DPI+
# sends F24; `sm809 daemon` turns them back into a middle click / DPI up, or
# into "next profile" when DPI+ is pressed while the wheel is held. Routing the
# wheel through the daemon keeps a chord from also middle-click-pasting. (The
# mouse reports DPI changes, but not a DPI+ press at the top stage.) F19 and
# F24 have no default binding on Linux desktops.
CHORD_MIDDLE_KEY = 0x6E
SMART_DPI_KEY = 0x73
CHORD_CODES = {14: [0x70, 0, CHORD_MIDDLE_KEY], 15: [0x70, 0, SMART_DPI_KEY]}

SIMPLE_ACTIONS = {
    "dpi-cycle": [0x40, 1, 0], "dpi-up": [0x40, 2, 0], "dpi-down": [0x40, 3, 0],
    "disable": [0x60, 0, 0], "lighting-toggle": [0x60, 4, 0],
    "triple-fire": [0x30, 50, 3],
}

# Macro run modes -> firmware mode nibble. "once" is "repeat" with a count of 1.
MACRO_MODES = {"once": 2, "repeat": 2, "hold": 1, "toggle": 4}


def key_combo(spec: str) -> list[int]:
    """'ctrl+shift+t' -> [0x70, modifier bits, HID usage]."""
    mods, key = 0, 0
    for part in spec.lower().split("+"):
        if part in MODIFIERS:
            mods |= MODIFIERS[part]
        elif part in KEYS and not key:
            key = KEYS[part]
        else:
            raise ProtocolError(f"bad key combo {spec!r} (at {part!r})")
    return [0x70, mods, key]


def action_code(action: str, button: int, macro_modes: dict[str, tuple] | None = None) -> list[int]:
    """Translate an action string into the 3-byte button code.

    macro_modes maps macro name -> (mode, repeat count[, memory block]) for
    'macro:NAME'. Without a block, the vendor tool's button-number block is used.
    """
    a = action.strip().lower()
    if a in MOUSE_MASK:
        return [0x10, MOUSE_MASK[a], 0]
    if a in SIMPLE_ACTIONS:
        return list(SIMPLE_ACTIONS[a])
    if a in SHORTCUTS:
        return key_combo(SHORTCUTS[a])
    if a in CONSUMER:
        u = CONSUMER[a]
        return [0x80, u & 0xFF, u >> 8]
    if m := re.fullmatch(r"consumer:(0x[0-9a-f]+|\d+)", a):
        u = int(m.group(1), 0)
        return [0x80, u & 0xFF, (u >> 8) & 0xFF]
    if m := re.fullmatch(r"fire:(\d+):(\d+)", a):
        interval, count = int(m.group(1)), int(m.group(2))
        if not (5 <= interval <= 255 and 0 <= count <= 255):
            raise ProtocolError("fire:INTERVAL:COUNT needs interval 5-255, count 0-255")
        return [0x30, interval, count]
    if m := re.fullmatch(r"dpi-lock:(\d+)", a):
        dpi = int(m.group(1))
        if dpi % 100 or not 200 <= dpi <= 1200:
            raise ProtocolError("dpi-lock supports 200-1200 in steps of 100")
        return [0x50, dpi // 100 - 1, 0]
    if m := re.fullmatch(r"raw:([0-9a-f]{2}),([0-9a-f]{2}),([0-9a-f]{2})", a):
        return [int(g, 16) for g in m.groups()]
    if a.startswith("macro:"):
        name = action.strip()[6:]
        if not macro_modes or name not in macro_modes:
            raise ProtocolError(f"macro {name!r} is not defined in this profile")
        if BUTTONS.get("wheel-up") == button or BUTTONS.get("wheel-down") == button:
            raise ProtocolError("macros cannot be bound to the wheel")
        mode, repeat, *block = macro_modes[name]
        count = {"once": 1, "repeat": repeat}.get(mode, 0)
        return [0x90, MACRO_MODES[mode] << 4 | (block[0] if block else macro_slot(button)), count]
    return key_combo(a)


def macro_slot(button: int) -> int:
    # The vendor tool stores button 16's macro in slot 12 (16 & 0xF would be 0).
    return 12 if button == 16 else button & 0x0F


def allocate_macro_blocks(sizes: dict[str, int]) -> dict[str, int]:
    """Give each macro (name -> data bytes) its own run of memory blocks.

    Macro memory is 25-byte blocks addressed 1-15; a macro starting at block b
    fills b, b+1, ... and later writes overwrite earlier ones (seen on
    hardware). The vendor tool starts each macro at its button number, so two
    macros on neighbouring buttons collide; packing them avoids that.
    """
    blocks, nxt = {}, 1
    for name, size in sizes.items():
        need = max(1, -(-size // MACRO_CHUNK))
        if nxt + need - 1 > 15:
            total = sum(sizes.values())
            raise ProtocolError(f"macros too long: {total} bytes in total, the mouse holds "
                                f"{MAX_MACRO_BYTES} ({MAX_MACRO_BYTES // 3} key events)")
        blocks[name], nxt = nxt, nxt + need
    return blocks


def buttons_packets(codes: dict[int, list[int]], seq) -> list[bytes]:
    """codes: button ID -> 3-byte action. seq: callable returning next seq."""
    out = []
    for sub, order in BUTTON_PACKETS:
        body = [CMD_BUTTONS, seq(), sub]
        for b in order:
            body += codes.get(b, [0xFF, 0xFF, 0xFF])
        out.append(frame(body, 0xFF))
    return out


# --------------------------------------------------------------------------
# DPI

SENSORS = {
    17: ("PAW3311", 24000), 149: ("PAW3395", 26000), 80: ("PAW3950", 30000),
    32: ("PAW3220", 8000), 18: ("PAW3212", 4800), 9: ("PMW3809", 6000),
    6: ("S205", 12800), 112: ("OM70", 30000),
}
MAX_STAGES = 6  # the SM809 config caps the UI at 6 of the 8 firmware slots


def dpi_step(sensor: int) -> int:
    return 50 if sensor in (149, 80) else 100


def dpi_packets(stages: list[int], current: int, sensor: int, seq) -> list[bytes]:
    """current is 0-based. Sends X then Y (identical; the SM809 has no XY split)."""
    if not 1 <= len(stages) <= MAX_STAGES:
        raise ProtocolError(f"need 1-{MAX_STAGES} DPI stages")
    if not 0 <= current < len(stages):
        raise ProtocolError("current DPI stage out of range")
    step = dpi_step(sensor)
    top = SENSORS.get(sensor, ("?", 26000))[1]
    for d in stages:
        if d % step or not step <= d <= top:
            raise ProtocolError(f"DPI {d} invalid: use multiples of {step} up to {top}")
    mask = (1 << len(stages)) - 1
    out = []
    for axis in (0x02, 0x12):
        body = [CMD_DPI, seq(), axis, current, mask]
        for i in range(8):
            v = stages[i] // step - 1 if i < len(stages) else -1
            body += [v & 0xFF, (v >> 8) & 0xFF]
        out.append(frame(body, 0xFF))
    return out


def dpi_colors_packet(colors: list[str], seq) -> bytes:
    colors = (list(colors) + ["#ea5e56"] * 8)[:8]
    body = [CMD_DPI_COLORS, seq(), 1]
    for c in colors:
        body += parse_color(c)
    return frame(body, 0xFF)


def parse_color(c: str) -> list[int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", c.strip())
    if not m:
        raise ProtocolError(f"bad color {c!r}, use #rrggbb")
    h = m.group(1)
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


# --------------------------------------------------------------------------
# Performance, power, lighting

RATES = {125: 8, 250: 4, 500: 2, 1000: 1, 2000: 17, 4000: 18, 8000: 20}
RATE_NAMES = {v: k for k, v in RATES.items()}


def rate_packet(hz: int, seq) -> bytes:
    if hz not in RATES:
        raise ProtocolError(f"polling rate must be one of {sorted(RATES)}")
    return frame([CMD_RATE, seq(), 1, RATES[hz]], 0x00)


def lod_packet(mm: int, seq) -> bytes:
    if mm not in (1, 2):
        raise ProtocolError("lift-off distance must be 1 or 2 (mm)")
    return frame([CMD_LOD, seq(), 1, mm], 0xFF)


def power_packet(sleep: int, motion_wake: bool, motion_lighting: bool, seq) -> bytes:
    if not 1 <= sleep <= 20:
        raise ProtocolError("sleep time must be 1-20")
    return frame([CMD_POWER, seq(), 1, sleep, int(motion_wake), int(motion_lighting)], 0xFF)


def sensor_packet(angle_snap: bool, motion_sync: bool, ripple: bool, seq) -> bytes:
    return frame([CMD_SENSOR, seq(), 1, int(angle_snap), int(motion_sync), int(ripple)], 0xFF)


LIGHT_MODES = {
    "off": 0, "stars-twinkle": 1, "colorful-streaming": 2,
    "seven-color-stretch": 3, "seven-color-flashing": 4, "colorful-breath": 5,
    "explosion-flash": 6, "colorful-coverage": 7,
    "red": 8, "green": 9, "blue": 10,  # 026E/026F revision
    "static": 15,  # custom colour: 809x revision only
}
LIGHT_NAMES = {v: k for k, v in LIGHT_MODES.items()}


def lighting_packet(mode: str, color: str, brightness: int | None, speed: int | None, seq) -> bytes:
    if mode not in LIGHT_MODES:
        raise ProtocolError(f"lighting mode must be one of {', '.join(LIGHT_MODES)}")
    m = LIGHT_MODES[mode]
    # The vendor tool sends 0 here for every SM809 mode; brightness 1-4 / speed 1-3
    # are only sent when explicitly configured. The fixed-colour modes stay dark
    # at brightness 0 (seen on hardware), so they default to full brightness.
    if m in (8, 9, 10, 15) and not brightness:
        brightness = 4
    level = (max(1, min(brightness, 4)) << 4 if brightness else 0) | (max(1, min(speed, 3)) if speed else 0)
    body = [CMD_LIGHTING, seq(), 1, 0xFF, m, level, 0]
    if m == 15:
        body += parse_color(color)
    return frame(body, 0xFF)


def reset_packet(seq) -> bytes:
    return frame([CMD_RESET, seq(), 1, 0xFF], 0xFF)


# --------------------------------------------------------------------------
# Macros

MACRO_CHUNK = 25
MAX_MACRO_BYTES = 15 * MACRO_CHUNK  # packet count lives in a nibble


def macro_events(events: list[str]) -> list[int]:
    """Encode macro steps into 3-byte firmware events.

    Steps: 'down:KEY', 'up:KEY', 'tap:KEY', 'delay:MS', 'move:left|right|up|down:N'.
    KEY may be a keyboard key, a modifier (ctrl/shift/alt/win) or a mouse
    button (mouse-left/right/middle/forward/back). Append '@MS' to down/up/tap
    to wait after the event (default 20 ms for tap).
    """
    out: list[int] = []
    for step in events:
        kind, _, rest = step.strip().lower().partition(":")
        if kind == "delay":
            ms = min(int(rest), 4095)
            out += [ms >> 8 & 0x0F, ms & 0xFF, 0]
        elif kind == "move":
            direction, _, n = rest.partition(":")
            n = int(n or 1)
            head = 0x40 if direction in ("left", "right") else 0x50
            if direction not in ("left", "right", "up", "down"):
                raise ProtocolError(f"bad move direction in {step!r}")
            out += [head, 0, (-n if direction in ("left", "up") else n) & 0xFF]
        elif kind in ("down", "up", "tap"):
            key, _, wait = rest.partition("@")
            wait = min(int(wait), 4095) if wait else (20 if kind == "tap" else 0)
            for up in ((False, True) if kind == "tap" else (kind == "up",)):
                out += _key_event(key, up, wait)
        else:
            raise ProtocolError(f"bad macro step {step!r}")
    if len(out) > MAX_MACRO_BYTES:
        raise ProtocolError(f"macro too long ({len(out) // 3} events, max {MAX_MACRO_BYTES // 3})")
    return out


_MACRO_MOUSE = {"mouse-left": 1, "mouse-right": 2, "mouse-middle": 4,
                "mouse-forward": 8, "mouse-back": 16}
_MACRO_MODS = {"ctrl": 1, "shift": 2, "alt": 4, "win": 8}


def _key_event(key: str, up: bool, wait: int) -> list[int]:
    # Type nibble: 0x30 key, 0x20 modifier bit, 0x10 mouse-button bit. 0x80 = release.
    # Mouse forward/back bits follow the vendor tool (8 = forward, 16 = back).
    if key in _MACRO_MOUSE:
        head, val = 0x10, _MACRO_MOUSE[key]
    elif key in _MACRO_MODS:
        head, val = 0x20, _MACRO_MODS[key]
    elif key in KEYS:
        head, val = 0x30, KEYS[key]
    else:
        raise ProtocolError(f"unknown macro key {key!r}")
    return [head | (0x80 if up else 0) | (wait >> 8 & 0x0F), wait & 0xFF, val]


# US layout: characters that need Shift, and what they're shifted from.
_SHIFTED = dict(zip('~!@#$%^&*()_+{}|:"<>?', '`1234567890-=[]\\;\',./'))
_PLAIN = {" ": "space", "\n": "enter", "\t": "tab"}


def text_steps(text: str, wait: int = 15) -> list[str]:
    """Macro steps that type `text` (US keyboard layout)."""
    steps, shifted = [], False
    for ch in text:
        if ch.isascii() and ch.isalpha():
            need, key = ch.isupper(), ch.lower()
        elif ch in _SHIFTED:
            need, key = True, _SHIFTED[ch]
        elif ch in _PLAIN or ch in KEYS:
            need, key = False, _PLAIN.get(ch, ch)
        else:
            raise ProtocolError(f"can't type {ch!r} (US layout ASCII only)")
        if need != shifted:
            steps.append(f"{'down' if need else 'up'}:shift")
            shifted = need
        steps.append(f"tap:{key}@{wait}")
    if shifted:
        steps.append("up:shift")
    return steps


def macro_erase_packet(seq) -> bytes:
    return frame([CMD_MACRO_ERASE, seq(), 1, 0xFF], 0x00)


def macro_packets(block: int, data: list[int], seq) -> list[bytes]:
    chunks = [data[i:i + MACRO_CHUNK] for i in range(0, len(data), MACRO_CHUNK)] or [[]]
    out = []
    for i, chunk in enumerate(chunks):
        body = [CMD_MACRO_DATA, seq(), (i << 4 & 0xF0) | (len(chunks) & 0x0F),
                1 + len(chunk), block] + chunk
        out.append(frame(body, 0x00))
    return out


# --------------------------------------------------------------------------
# Status


def parse_status(data: bytes) -> dict:
    """Decode input report 4 (payload without the report ID)."""
    sensor = data[8]
    return {
        "link": data[0],
        "dpi_stage": data[1] + 1,
        "polling_rate": RATE_NAMES.get(data[2], f"code {data[2]}"),
        "battery": data[3],
        "charging": data[4],
        "lighting": LIGHT_NAMES.get(data[5], f"code {data[5]}"),
        "max_polling_rate": RATE_NAMES.get(data[7], f"code {data[7]}"),
        "sensor": SENSORS.get(sensor, (f"type {sensor}",))[0],
        "sensor_type": sensor,
    }

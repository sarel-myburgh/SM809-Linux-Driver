# SM809 Pro HID protocol

Reverse-engineered from the vendor WebHID configurators linked from
solakakadriver.com (fetched 2026-10-28):

- `driver.yuandaxin-tech.com/HYX/V1000_SM809_3311/` — PIDs 026E/026F
  (`assets/index-CQl4Vnyy.js`, `assets/DeviceView-C4JAQhLH.js`)
- `driver.yuandaxin-tech.com/HYX/V1000_SM809_3311_Full/` — PIDs 809D/E/F
  (`assets/index-nqXeY84a.js`, `assets/DeviceView-x0w9MzMV.js`)

Both use the same HID service (function `YG`) and byte-identical setting
commands. Verified on a wired unit (026E, bcdDevice 0x0109): status, polling
rate and lighting writes.

## Devices

| VID:PID | Transport |
|---|---|
| 04F3:026E | USB cable ("MMO Gaming Mouse"; also the ID in the 2025-08-11 wired firmware) |
| 04F3:026F | 2.4G receiver |
| 04F3:026D | Bluetooth LE ("SM809 BT1"; HID over GATT, read-only, see below) |
| 04F3:809E | USB cable (other revision) |
| 04F3:809F | 2.4G receiver (other revision) |
| 04F3:809D | Bluetooth |

The mouse exposes two interfaces. The second one (keyboard/consumer/system
collections plus usage page 0x0A vendor collections) carries:

| Report | Type | Size | Use |
|---|---|---|---|
| 4 | Input | 31 | status, pushed by the device |
| 5 | Feature | 31 | reading it triggers a status report |
| 6 | Feature | 31 | commands (SET) and ack (GET) |
| 0xBC–0xBF | Feature | 7/135 | used by the IAP firmware updater, not by configuration |

### Bluetooth

Over BLE the mouse exposes one combined HID interface with the same reports
4/5/6. Reading works: the status report arrives about 2 s after the feature 5
read. **Writes to feature report 6 are refused by the mouse.** BlueZ's HoG
path logs `set_report_cb() Error setting Report value: Internal application
error: I/O` (hidraw returns EIO), and a direct GATT write to the report
characteristic (Report Reference `06 03`) fails with "Not Authorized". The
refusal doesn't depend on length (19–31 bytes all fail). Configure the mouse
over the cable or the receiver; the settings are stored on the mouse and apply
in Bluetooth mode too. Over Bluetooth the max-polling-rate status byte reads
0x7D, which isn't a USB rate code.

## Command framing (feature report 6)

```
[cmd] [seq] [sub] [checksum] [args…]   31 bytes, padded with 0xFF or 0x00
checksum = (6 + sum of all other bytes) & 0xFF
```

`seq` increments per command (wraps at 256). **The firmware silently drops a
command whose seq equals the last one it accepted** (any other value works,
including jumps), so a tool that restarts its counter at 1 on every run has
each run after the first ignored. `sm809` keeps the counter in `$XDG_RUNTIME_DIR`.

After each SET the vendor tool waits 60 ms, reads feature 6 and treats byte 1
== 1 as success, retrying up to 3 times. On firmware 0109 that byte is always
1, even for a bad checksum or a dropped command, so it proves nothing.
Read the status report to confirm a change.

| cmd | sub | args | pad |
|---|---|---|---|
| 0x01 buttons | 0x02 / 0x12 | 9 × 3-byte action per packet (slot order below) | FF |
| 0x02 DPI | 0x02 X / 0x12 Y | current stage (0-based), enabled mask, 8 × u16 LE (dpi/100 − 1; /50 for sensors 0x95, 0x50); unused = FFFF | FF |
| 0x03 DPI colors | 0x01 | 8 × RGB | FF |
| 0x04 polling rate | 0x01 | 8=125, 4=250, 2=500, 1=1000, 0x11=2000, 0x12=4000, 0x14=8000 | 00 |
| 0x05 lift-off | 0x01 | 1 or 2 (mm) | FF |
| 0x06 lighting | 0x01 | FF, mode, brightness<<4 \| speed, direction, [R G B for mode 15] | FF |
| 0x07 power | 0x01 | sleep (1–20), motion wake, "lighting off while moving" | FF |
| 0x08 sensor | 0x01 | angle snap, motion sync, ripple control | FF |
| 0x0F reset | 0x01 | FF | FF |
| 0x10 macro data | (pkt<<4) \| total | 1+len, slot, ≤25 data bytes | 00 |
| 0x11 macro erase | 0x01 | FF | 00 |

Button slot order — packet 0x02: 12 13 14 15 16 10 11 1 2; packet 0x12: 3 4 5 6 9 7 8 17 18
(button numbers as in `buttons.png`). On the 026E/026F revision the vendor tool
maps only buttons 1–16 and leaves the 17/18 (wheel) slots at FF.

Lighting modes: 0 off, 1 stars twinkle, 2 colorful streaming, 3 seven-color
stretch, 4 seven-color flashing, 5 colorful breath, 6 explosion flash,
7 colorful coverage; 026E/026F revision: 8 red, 9 green, 10 blue; 809x
revision: 15 static colour (RGB follows). The fixed-colour modes stay dark
unless the brightness nibble is non-zero (the vendor tool sends 0).

## Button actions (3 bytes)

| bytes | action |
|---|---|
| 10 mask 00 | mouse: 1 L, 2 R, 4 M, 8 back, 16 forward, 32 wheel up, 64 wheel down |
| 30 interval count | fire key (count 0 = while held); triple fire = 30 32 03 |
| 40 n 00 | DPI: 1 cycle, 2 up, 3 down |
| 50 n 00 | DPI lock at (n+1)×100 |
| 60 00 00 / 60 04 00 | disable / lighting toggle |
| 70 mods key | keyboard: mods 1 Ctrl, 2 Shift, 4 Alt, 8 Win; key = HID usage |
| 80 lo hi | consumer usage (e.g. 0xCD play/pause) |
| 90 mode<<4\|block count | macro: mode 1 = repeat while held, 2 = run `count` times, 4 = repeat until any key; block = start of the macro's data (vendor tool: button & 0xF, button 16 → 12) |

## Macros

Erase (0x11) once, then send each bound macro as 0x10 packets of up to 25
data bytes. The packet's block byte is where the macro starts. Macro memory
is 25-byte blocks 1–15: a macro starting at block b fills b, b+1, …, and a
later write to an overlapping block overwrites it (seen on hardware: two
macros on neighbouring buttons truncated the first one). The block in the
button code doesn't have to match the button, so `sm809` packs macros into
consecutive blocks (375 bytes total). Data is a list of 3-byte events:

| bytes | event |
|---|---|
| 0d dd 00 | delay (12-bit ms: high nibble in byte 0) |
| 3d dd key | key; 0x80 bit set = release; dd = delay after (12-bit) |
| 2d dd bit | modifier (1 Ctrl, 2 Shift, 4 Alt, 8 Win) |
| 1d dd bit | mouse button (1 L, 2 R, 4 M, 8 forward, 16 back, as the vendor tool encodes it) |
| 40 00 dx / 50 00 dy | cursor move (signed byte) |

## Status (input report 4)

`link, dpi stage (0-based), polling rate code, battery %, charging, lighting
mode, ?, max polling rate code, sensor type` — sensor 0x11 = PAW3311 (the
SM809 Pro), 0x95 PAW3395, 0x50 PAW3950.

The mouse sends it every 5 s, after a feature 5 read, and immediately on a
DPI stage change. DPI+ at the top stage (or DPI− at the bottom) changes
nothing and sends no report.

## Button behaviour

Key and combo actions are held while the button is held (a modifier-only
action like `70 02 00` works as a held Shift). The mouse's own buttons, DPI
keys and wheel are invisible to the host unless remapped to a key.

## Profiles

There is no on-board profile command. The vendor tool keeps profiles in
browser storage and pushes all settings when switching, and `sm809` works the same way.

# sm809 — Solakaka SM809 Pro on Linux

Configure the SM809 Pro from Linux: DPI stages, polling rate, lift-off,
lighting, power, all buttons, macros and named profiles. It works over the USB
cable and the 2.4G receiver; over Bluetooth it can only read status (the mouse
refuses changes there).

The mouse already works as a normal HID mouse, so no kernel module is needed.
`sm809` talks to the mouse's vendor hidraw interface using the same commands
as the official web configurator ([docs/PROTOCOL.md](docs/PROTOCOL.md)). The
command-line tool only needs Python 3.11+; the GUI also needs PyQt6.

Beyond the vendor tool it can:
- bind any key, combo or lone modifier (`ctrl+shift+c`, `ctrl+backspace`,
  `shift`, `f13`) — held for as long as the button is held
- bind system actions: lock, power, sleep, screenshot, brightness, browser keys
- type a string (`text:my.username`)
- switch profiles with **wheel-click + DPI+**
- store several long macros at once (the vendor tool overlaps them)

## AppImage (GUI)

Download `SM809-x86_64.AppImage` from the
[latest release](https://github.com/sarel-myburgh/SM809-Linux-Driver/releases/latest), then:

```bash
sudo apt install python3-pyqt6         # the AppImage uses the system's Python and PyQt6
chmod +x SM809-x86_64.AppImage
./SM809-x86_64.AppImage                # opens the GUI
```

Or build it yourself: `./build-appimage.sh` → `dist/SM809-x86_64.AppImage`.

The AppImage contains the sm809 code and uses the system's `python3` and
PyQt6 (`sudo apt install python3-pyqt6`). Any argument runs the command-line
tool instead, e.g. `./dist/SM809-x86_64.AppImage status`.

First run, in the GUI's **Settings** tab:
1. **Install device permission rule**, if the header says "no permission"
   (asks for your password once).
2. **Add to application menu**, if you want a launcher.
3. Tick **Hold the wheel button and press DPI+…** to turn on profile switching.
   It starts a small background service and adds it to your login autostart.

Moving the AppImage later breaks the autostart entry and the menu launcher.
Re-tick the switching box and click "Add to application menu" again from the
new location.

## Profile switching

With switching on, the wheel button and DPI+ send F19 and F24 (no default
meaning on Linux) and the background service (`sm809 daemon`) turns them back
into normal behaviour:

| You do | Result |
|---|---|
| hold wheel, press DPI+ | next profile, with a desktop notification; no middle click |
| quick wheel click | middle click |
| hold wheel ~0.3 s | middle button held (panning, autoscroll in apps that support it) |
| DPI+ alone | next DPI stage |

So with switching on, the wheel button and DPI+ only work while the service is
running. Turn switching off to give them back to the mouse.

The bindings are stored on the mouse, so they apply in every mode. Over
Bluetooth the service still turns the wheel into a middle click. But DPI+ only
shows a notification, because DPI changes and profile switches need the cable
or the receiver (the mouse refuses writes over Bluetooth).

## Command line

```bash
sudo ./install-udev.sh                # once: lets your user open the mouse without root
python3 -m sm809 devices              # or install it: pipx install .
sm809 status
sm809 actions                         # button names and everything you can bind
sm809 bind side1 ctrl+shift+c         # binds and applies immediately
sm809 bind side2 text:my.username
sm809 bind edge-back lock
sm809 bind side9 dpi-lock:400         # sniper button
sm809 dpi 800 1600 3200 6400 --current 2
sm809 rate 1000
sm809 lighting red                    # this revision: red/green/blue, no custom colour

sm809 profile new fps --from default  # profiles live in ~/.config/sm809/profiles/
sm809 profile use fps                 # pushes everything and makes it active
sm809 chord on                        # wheel-click + DPI+ switching (adds autostart)

sm809 -n apply fps                    # dry run: print the packets, send nothing
```

Button numbering ([docs/buttons.png](docs/buttons.png)): `side1`–`side9` are the
side keypad, `edge-front`/`edge-back` sit beside left click, plus `left`,
`right`, `middle`, `dpi-up`, `dpi-down`, `wheel-up`, `wheel-down`.

Macros go in the profile (the GUI's Macros tab can record them):

```toml
[buttons]
side3 = "macro:gg"

[macros.gg]
mode = "once"          # once | repeat (with repeat = N) | hold (while held) | toggle (until any key)
steps = ["tap:enter", "tap:g", "tap:g", "tap:enter"]
```

All macros and `text:` bindings together can hold 375 bytes (about 125 key
events, or ~60 typed characters).

Settings are stored on the mouse, so they survive reboots and work on other
computers. The mouse has no on-board profile slots; switching profiles
re-sends the settings. Editing a profile with `bind`/`dpi`/`rate`/`lighting`
rewrites the TOML file and drops any comments in it.

## Status

- Tested on this unit (`04f3:026e` cable, `04f3:026f` receiver, firmware 0109):
  status, DPI, polling rate, lighting, button binds (keys, combos, held
  modifiers, media keys), macros, `text:`, and wheel+DPI+ profile switching.
- Bluetooth (`04f3:026d`, BLE): status only; the mouse refuses changes.
- On this revision the wheel up/down can't be remapped (the vendor tool can't
  either), lighting has fixed red/green/blue instead of a custom colour, and the
  default profile matches its factory layout.
- Untested on hardware: lock/power/sleep and the other system actions (they
  use standard key codes), right-hand modifiers, the 809x mouse revision.
- Packet encoders are unit-tested: `python3 -m unittest`.

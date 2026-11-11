"""Qt GUI for the SM809 Pro: profiles, buttons, DPI, lighting, macros, switching."""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys
import threading
import time
from importlib import resources

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QColor, QFontDatabase, QIcon, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QTabWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from . import daemon, hidraw, profile
from . import protocol as p

APP_NAME = "SM809 Pro"


def asset(name: str) -> str:
    return str(resources.files(__package__).joinpath(name))


# Linux evdev key codes (KEY_*) -> sm809 key names, for recording keys.
EVDEV_KEYS = {1: "esc", 12: "-", 13: "=", 14: "backspace", 15: "tab", 26: "[", 27: "]",
              28: "enter", 39: ";", 40: "'", 41: "`", 43: "\\", 51: ",", 52: ".", 53: "/",
              55: "kp*", 57: "space", 58: "capslock", 69: "numlock", 70: "scrolllock",
              71: "kp7", 72: "kp8", 73: "kp9", 74: "kp-", 75: "kp4", 76: "kp5", 77: "kp6",
              78: "kp+", 79: "kp1", 80: "kp2", 81: "kp3", 82: "kp0", 83: "kp.", 87: "f11",
              88: "f12", 96: "kpenter", 98: "kp/", 99: "printscreen", 102: "home",
              103: "arrow-up", 104: "pageup", 105: "arrow-left", 106: "arrow-right", 107: "end",
              108: "arrow-down", 109: "pagedown", 110: "insert", 111: "delete", 113: "mute",
              114: "volumedown", 115: "volumeup", 119: "pause", 127: "menu"}
EVDEV_KEYS.update({2 + i: str((i + 1) % 10) for i in range(10)})
EVDEV_KEYS.update({59 + i: f"f{i + 1}" for i in range(10)})
EVDEV_KEYS.update({183 + i: f"f{13 + i}" for i in range(12)})
for row, start in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
    EVDEV_KEYS.update({start + i: ch for i, ch in enumerate(row)})
EVDEV_MODS = {29: "ctrl", 97: "rctrl", 42: "shift", 54: "rshift", 56: "alt", 100: "altgr",
              125: "win", 126: "rwin"}


def key_name(ev: QKeyEvent) -> str | None:
    code = ev.nativeScanCode() - 8  # X11/Wayland keycode -> evdev code
    if code in EVDEV_MODS:
        return EVDEV_MODS[code]
    if code in EVDEV_KEYS:
        return EVDEV_KEYS[code]
    text = ev.text().lower()
    return text if text in p.KEYS else None


def combo_from_event(ev: QKeyEvent) -> str | None:
    key = key_name(ev)
    if key is None:
        return None
    mods = ev.modifiers()
    parts = [name for flag, name in ((Qt.KeyboardModifier.ControlModifier, "ctrl"), (Qt.KeyboardModifier.ShiftModifier, "shift"),
                                      (Qt.KeyboardModifier.AltModifier, "alt"), (Qt.KeyboardModifier.MetaModifier, "win"))
             if mods & flag and name != key]
    return "+".join(parts + [key])


class KeyRecorder(QPushButton):
    """Click, then press a key or combo; emits it as an sm809 combo string."""
    recorded = Signal(str)

    def __init__(self):
        super().__init__("Record keys…")
        self.setCheckable(True)
        self.toggled.connect(lambda on: self.setText("Press keys… (Esc cancels)" if on else "Record keys…"))
        self.toggled.connect(lambda on: self.grabKeyboard() if on else self.releaseKeyboard())

    def keyPressEvent(self, ev: QKeyEvent):
        if not self.isChecked():
            return super().keyPressEvent(ev)
        if ev.key() == Qt.Key.Key_Escape and not ev.modifiers():
            self.setChecked(False)
            return
        combo = combo_from_event(ev)
        if combo and key_name(ev) not in EVDEV_MODS.values():
            self.setChecked(False)
            self.recorded.emit(combo)

    def keyReleaseEvent(self, ev: QKeyEvent):
        # A modifier pressed and released on its own (e.g. just Shift).
        if self.isChecked() and key_name(ev) in EVDEV_MODS.values():
            self.setChecked(False)
            self.recorded.emit(key_name(ev))


class MacroRecorder(QPushButton):
    """Records key presses/releases with timing as macro steps."""
    step = Signal(str)

    def __init__(self):
        super().__init__("● Record")
        self.setCheckable(True)
        self.last = None
        self.toggled.connect(self._toggle)

    def _toggle(self, on: bool):
        self.setText("■ Stop recording" if on else "● Record")
        self.last = None
        self.grabKeyboard() if on else self.releaseKeyboard()

    def _record(self, ev: QKeyEvent, kind: str):
        if not self.isChecked() or ev.isAutoRepeat():
            return
        key = key_name(ev)
        if key is None:
            return
        now = time.monotonic()
        if self.last is not None:
            ms = min(int((now - self.last) * 1000), 4095)
            if ms > 5:
                self.step.emit(f"delay:{ms}")
        self.last = now
        self.step.emit(f"{kind}:{key}")

    def keyPressEvent(self, ev: QKeyEvent):
        self._record(ev, "down")

    def keyReleaseEvent(self, ev: QKeyEvent):
        self._record(ev, "up")


class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, color: str = "#ffffff"):
        super().__init__()
        self.setFixedWidth(48)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color: str):
        self.color = color
        self.setStyleSheet(f"background:{color}; border:1px solid #888; min-height:20px")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self)
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(c.name())


# ---------------------------------------------------------------- action editor

TYPES = ["Mouse button", "Key / combo", "Shortcut", "System & media", "Type text", "Macro",
         "DPI", "Fire", "Special", "Advanced"]
SPECIAL = ["disable", "lighting-toggle"]
DPI_ACTIONS = ["dpi-cycle", "dpi-up", "dpi-down", "dpi-lock"]


def classify(action: str) -> int:
    a = action.strip().lower()
    if a in p.MOUSE_MASK:
        return 0
    if a.startswith("text:"):
        return 4
    if a.startswith("macro:"):
        return 5
    if a in p.SHORTCUTS:
        return 2
    if a in p.CONSUMER:
        return 3
    if a in DPI_ACTIONS or a.startswith("dpi-lock:"):
        return 6
    if a.startswith("fire:") or a == "triple-fire":
        return 7
    if a in SPECIAL:
        return 8
    try:
        p.key_combo(a)
        return 1
    except p.ProtocolError:
        return 9


class ActionEditor(QWidget):
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self._loading = False
        lay = QVBoxLayout(self)
        self.type = QComboBox()
        self.type.addItems(TYPES)
        lay.addWidget(self.type)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)

        self.mouse = QComboBox()
        self.mouse.addItems(list(p.MOUSE_MASK))
        self.stack.addWidget(self.mouse)

        w = QWidget()
        kl = QVBoxLayout(w)
        self.combo = QLineEdit()
        self.combo.setPlaceholderText("e.g. ctrl+shift+c, ctrl+backspace, shift, space, f13")
        self.recorder = KeyRecorder()
        self.recorder.recorded.connect(self.combo.setText)
        kl.addWidget(self.combo)
        kl.addWidget(self.recorder)
        kl.addWidget(QLabel("Held for as long as the mouse button is held."))
        self.stack.addWidget(w)

        self.shortcut = QComboBox()
        for name, combo in p.SHORTCUTS.items():
            self.shortcut.addItem(f"{name}  ({combo})", name)
        self.stack.addWidget(self.shortcut)

        self.system = QComboBox()
        for name in p.CONSUMER:
            self.system.addItem(name, name)
        self.stack.addWidget(self.system)

        w = QWidget()
        tl = QVBoxLayout(w)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Text to type, e.g. your username (US layout, ASCII)")
        tl.addWidget(self.text)
        tl.addWidget(QLabel("Typed once per press. Up to ~60 characters, shared with macros."))
        self.stack.addWidget(w)

        self.macro = QComboBox()
        self.stack.addWidget(self.macro)

        w = QWidget()
        dl = QHBoxLayout(w)
        self.dpi = QComboBox()
        self.dpi.addItems(DPI_ACTIONS)
        self.dpi_lock = QSpinBox()
        self.dpi_lock.setRange(200, 1200)
        self.dpi_lock.setSingleStep(100)
        self.dpi_lock.setSuffix(" DPI")
        dl.addWidget(self.dpi)
        dl.addWidget(self.dpi_lock)
        self.stack.addWidget(w)

        w = QWidget()
        fl = QFormLayout(w)
        self.fire_interval = QSpinBox()
        self.fire_interval.setRange(5, 255)
        self.fire_interval.setSuffix(" ms")
        self.fire_count = QSpinBox()
        self.fire_count.setRange(0, 255)
        self.fire_count.setSpecialValueText("while held")
        fl.addRow("Interval", self.fire_interval)
        fl.addRow("Clicks", self.fire_count)
        self.stack.addWidget(w)

        self.special = QComboBox()
        self.special.addItems(SPECIAL)
        self.stack.addWidget(self.special)

        self.raw = QLineEdit()
        self.raw.setPlaceholderText("Any sm809 action string (see `sm809 actions`)")
        self.stack.addWidget(self.raw)

        self.error = QLabel()
        self.error.setStyleSheet("color:#d33")
        self.error.setWordWrap(True)
        lay.addWidget(self.error)
        lay.addStretch()

        self.type.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.type.currentIndexChanged.connect(self._emit)
        for wdg in (self.mouse, self.shortcut, self.system, self.macro, self.dpi, self.special):
            wdg.currentIndexChanged.connect(self._emit)
        for wdg in (self.combo, self.text, self.raw):
            wdg.textChanged.connect(self._emit)
        for wdg in (self.dpi_lock, self.fire_interval, self.fire_count):
            wdg.valueChanged.connect(self._emit)

    def set_macros(self, names: list[str]):
        self._loading = True
        cur = self.macro.currentText()
        self.macro.clear()
        self.macro.addItems(names)
        self.macro.setCurrentText(cur)
        self._loading = False

    def set_action(self, action: str):
        self._loading = True
        a = action.strip()
        t = classify(a)
        self.type.setCurrentIndex(t)
        self.stack.setCurrentIndex(t)
        low = a.lower()
        if t == 0:
            self.mouse.setCurrentText(low)
        elif t == 1:
            self.combo.setText(low)
        elif t == 2:
            self.shortcut.setCurrentIndex(self.shortcut.findData(low))
        elif t == 3:
            self.system.setCurrentIndex(self.system.findData(low))
        elif t == 4:
            self.text.setText(a[5:])
        elif t == 5:
            self.macro.setCurrentText(a[6:])
        elif t == 6:
            self.dpi.setCurrentText("dpi-lock" if low.startswith("dpi-lock") else low)
            if ":" in low:
                self.dpi_lock.setValue(int(low.split(":")[1]))
        elif t == 7:
            interval, count = (50, 3) if low == "triple-fire" else map(int, low.split(":")[1:])
            self.fire_interval.setValue(interval)
            self.fire_count.setValue(count)
        elif t == 8:
            self.special.setCurrentText(low)
        else:
            self.raw.setText(a)
        self._loading = False

    def action(self) -> str:
        t = self.type.currentIndex()
        return [
            lambda: self.mouse.currentText(),
            lambda: self.combo.text().strip().lower(),
            lambda: self.shortcut.currentData() or "",
            lambda: self.system.currentData() or "",
            lambda: "text:" + self.text.text(),
            lambda: "macro:" + self.macro.currentText(),
            lambda: (f"dpi-lock:{self.dpi_lock.value()}" if self.dpi.currentText() == "dpi-lock"
                     else self.dpi.currentText()),
            lambda: f"fire:{self.fire_interval.value()}:{self.fire_count.value()}",
            lambda: self.special.currentText(),
            lambda: self.raw.text().strip(),
        ][t]()

    def show_error(self, msg: str):
        self.error.setText(msg)

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit(self.action())


# ---------------------------------------------------------------- main window

class Bridge(QObject):
    done = Signal(object, object)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(asset("icon.png")))
        self.resize(880, 780)
        self.bridge = Bridge()
        self.bridge.done.connect(lambda cb, res: cb(res))
        self.cand: hidraw.Candidate | None = None
        self.status: dict | None = None
        self.name = profile.active_name()
        self.prof: dict = {}
        self.dirty = False
        self.busy = False
        self._loading = False

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.addLayout(self._build_header())
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self.tabs.addTab(self._build_buttons(), "Buttons")
        self.tabs.addTab(self._build_dpi(), "DPI")
        self.tabs.addTab(self._build_performance(), "Performance && power")
        self.tabs.addTab(self._build_lighting(), "Lighting")
        self.tabs.addTab(self._build_macros(), "Macros")
        self.tabs.addTab(self._build_settings(), "Settings")

        self.load_profile(self.name)
        self.refresh_device()
        timer = QTimer(self)
        timer.timeout.connect(self._poll)
        timer.start(2000)
        self._seen_active = self.name

    # ---------------------------------------------------------- helpers

    def run(self, fn, on_done, busy_msg: str | None = None):
        if busy_msg:
            self.busy = True
            self.apply_btn.setEnabled(False)
            self.statusBar().showMessage(busy_msg)

        def work():
            try:
                res = fn()
            except Exception as e:  # delivered to the UI thread
                res = e
            self.bridge.done.emit(on_done, res)
        threading.Thread(target=work, daemon=True).start()

    def mark_dirty(self):
        if not self._loading:
            self.dirty = True
            self._update_title()

    def _update_title(self):
        active = profile.active_name()
        self.setWindowTitle(f"{APP_NAME} — {self.name}{' *' if self.dirty else ''}"
                            f"{'  (active)' if self.name == active else ''}")
        self.active_label.setText(f"Active on mouse: <b>{active}</b>")

    def wheel_editable(self) -> bool:
        return not (self.cand and self.cand.pid in p.NO_WHEEL_BINDING_PIDS)

    # ---------------------------------------------------------- header

    def _build_header(self):
        outer = QVBoxLayout()
        row = QHBoxLayout()
        self.device_label = QLabel("Looking for the mouse…")
        self.device_label.setWordWrap(True)
        row.addWidget(self.device_label, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_device)
        row.addWidget(refresh)
        outer.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(140)
        self.profile_combo.activated.connect(lambda i: self.switch_profile(self.profile_combo.itemText(i)))
        row.addWidget(self.profile_combo)
        manage = QToolButton()
        manage.setText("Profile ▾")
        manage.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(manage)
        for text, fn in (("New…", self.new_profile), ("Duplicate…", self.dup_profile),
                         ("Rename…", self.rename_profile), ("Delete", self.delete_profile)):
            menu.addAction(text, fn)
        manage.setMenu(menu)
        row.addWidget(manage)
        row.addStretch()
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        row.addWidget(save)
        self.apply_btn = QPushButton("Save && apply")
        self.apply_btn.setDefault(True)
        self.apply_btn.clicked.connect(lambda: self.apply())
        row.addWidget(self.apply_btn)
        outer.addLayout(row)
        self.active_label = QLabel()
        outer.addWidget(self.active_label)
        return outer

    def refresh_profiles(self):
        self.profile_combo.clear()
        self.profile_combo.addItems(profile.list_profiles())
        self.profile_combo.setCurrentText(self.name)
        self._update_title()

    # ---------------------------------------------------------- buttons tab

    def _build_buttons(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        img = QLabel()
        img.setPixmap(QPixmap(asset("buttons.png")).scaled(260, 260, Qt.AspectRatioMode.KeepAspectRatio,
                                                           Qt.TransformationMode.SmoothTransformation))
        img.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(img)
        right = QVBoxLayout()
        self.button_list = QListWidget()
        self.button_list.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.button_list.currentRowChanged.connect(self._select_button)
        right.addWidget(self.button_list, 1)
        box = QGroupBox("Action")
        bl = QVBoxLayout(box)
        self.button_note = QLabel()
        self.button_note.setWordWrap(True)
        bl.addWidget(self.button_note)
        self.editor = ActionEditor()
        self.editor.changed.connect(self._action_changed)
        bl.addWidget(self.editor)
        reset = QPushButton("Reset to factory default")
        reset.clicked.connect(lambda: self.editor.set_action(p.DEFAULT_BUTTONS[self.current_button()])
                              or self._action_changed(self.editor.action()))
        bl.addWidget(reset)
        right.addWidget(box)
        lay.addLayout(right, 1)
        return w

    def fill_button_list(self):
        row = max(self.button_list.currentRow(), 0)
        self.button_list.blockSignals(True)
        self.button_list.clear()
        chord = profile.settings()["chord"]
        for b in range(1, 19):
            name = p.BUTTON_NAMES[b]
            action = self.prof["buttons"].get(name, p.DEFAULT_BUTTONS[b])
            note = ""
            if chord and b in p.CHORD_CODES:
                note = "  [profile switching]"
            if b in (17, 18) and not self.wheel_editable():
                note = "  [fixed]"
            item = QListWidgetItem(f"{b:>2}  {name:<11} {action}{note}")
            item.setData(Qt.ItemDataRole.UserRole, b)
            self.button_list.addItem(item)
        self.button_list.blockSignals(False)
        self.button_list.setCurrentRow(row)
        self._select_button(row)

    def current_button(self) -> int:
        item = self.button_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else 1

    def _select_button(self, row: int):
        if row < 0:
            return
        b = self.current_button()
        name = p.BUTTON_NAMES[b]
        self.editor.set_macros(list(self.prof.get("macros", {})))
        self.editor.set_action(self.prof["buttons"].get(name, p.DEFAULT_BUTTONS[b]))
        self.editor.show_error("")
        notes = []
        fixed = b in (17, 18) and not self.wheel_editable()
        if fixed:
            notes.append("This mouse revision can't remap the wheel (the vendor tool can't either).")
        if profile.settings()["chord"] and b in p.CHORD_CODES:
            notes.append("Wheel + DPI+ profile switching is on, so this button is handled by the "
                         "switcher (Settings tab). This binding applies when switching is off.")
        self.button_note.setText("\n".join(notes))
        self.editor.setEnabled(not fixed)

    def _action_changed(self, action: str):
        b = self.current_button()
        name = p.BUTTON_NAMES[b]
        try:
            if action.startswith("text:"):
                p.text_steps(action[5:])
                if not action[5:]:
                    raise p.ProtocolError("enter some text")
            else:
                modes = {n: (m.get("mode", "once"), 1) for n, m in self.prof.get("macros", {}).items()}
                p.action_code(action, b, modes)
        except p.ProtocolError as e:
            self.editor.show_error(str(e))
            return
        self.editor.show_error("")
        for k in list(self.prof["buttons"]):
            if p.button_id(k) == b:
                del self.prof["buttons"][k]
        self.prof["buttons"][name] = action
        item = self.button_list.currentItem()
        if item:
            item.setText(f"{b:>2}  {name:<11} {action}")
        self.mark_dirty()

    # ---------------------------------------------------------- DPI tab

    def _build_dpi(self):
        outer = QWidget()
        w = QWidget()
        w.setMaximumWidth(420)
        QHBoxLayout(outer).addWidget(w, 0, Qt.AlignmentFlag.AlignLeft)
        lay = QVBoxLayout(w)
        top = QFormLayout()
        self.stage_count = QSpinBox()
        self.stage_count.setRange(1, p.MAX_STAGES)
        self.stage_count.valueChanged.connect(self._dpi_count_changed)
        self.stage_current = QSpinBox()
        self.stage_current.setRange(1, p.MAX_STAGES)
        self.stage_current.valueChanged.connect(self._dpi_changed)
        top.addRow("Stages", self.stage_count)
        top.addRow("Start on stage", self.stage_current)
        lay.addLayout(top)
        grid = QGridLayout()
        self.stage_spins, self.stage_colors = [], []
        for i in range(p.MAX_STAGES):
            spin = QSpinBox()
            spin.setRange(100, 26000)
            spin.setSingleStep(100)
            spin.setSuffix(" DPI")
            spin.valueChanged.connect(self._dpi_changed)
            col = ColorButton()
            col.changed.connect(self._dpi_changed)
            grid.addWidget(QLabel(f"Stage {i + 1}"), i, 0)
            grid.addWidget(spin, i, 1)
            grid.addWidget(col, i, 2)
            self.stage_spins.append(spin)
            self.stage_colors.append(col)
        lay.addLayout(grid)
        self.dpi_error = QLabel()
        self.dpi_error.setStyleSheet("color:#d33")
        lay.addWidget(self.dpi_error)
        lay.addStretch()
        return outer

    def _dpi_count_changed(self, n: int):
        for i, (s, c) in enumerate(zip(self.stage_spins, self.stage_colors)):
            s.setEnabled(i < n)
            c.setEnabled(i < n)
        self.stage_current.setMaximum(n)
        self._dpi_changed()

    def _dpi_changed(self, *_):
        if self._loading:
            return
        n = self.stage_count.value()
        sensor = (self.status or {}).get("sensor_type", 17)
        step = p.dpi_step(sensor)
        stages = [s.value() // step * step for s in self.stage_spins[:n]]
        d = self.prof["dpi"]
        d["stages"], d["current"] = stages, self.stage_current.value()
        d["colors"] = [c.color for c in self.stage_colors[:n]]
        try:
            p.dpi_packets(stages, d["current"] - 1, sensor, lambda: 0)
            self.dpi_error.setText("")
        except p.ProtocolError as e:
            self.dpi_error.setText(str(e))
        self.mark_dirty()

    def load_dpi(self):
        d = self.prof["dpi"]
        stages = d["stages"]
        self.stage_count.setValue(len(stages))
        self.stage_current.setValue(d["current"])
        colors = d.get("colors", [])
        for i, (s, c) in enumerate(zip(self.stage_spins, self.stage_colors)):
            s.setValue(stages[i] if i < len(stages) else 3200)
            c.set_color(colors[i] if i < len(colors) else "#ffffff")
        self._dpi_count_changed(len(stages))

    # ---------------------------------------------------------- performance tab

    def _build_performance(self):
        w = QWidget()
        f = QFormLayout(w)
        self.rate = QComboBox()
        for hz in sorted(p.RATES):
            self.rate.addItem(f"{hz} Hz", hz)
        self.lod = QComboBox()
        self.lod.addItem("1 mm", 1)
        self.lod.addItem("2 mm", 2)
        self.angle = QCheckBox("Angle snap")
        self.motion = QCheckBox("Motion sync")
        self.ripple = QCheckBox("Ripple control")
        self.sleep = QSpinBox()
        self.sleep.setRange(1, 20)
        self.wake = QCheckBox("Wake on movement")
        self.move_light = QCheckBox("Lighting off while moving")
        f.addRow("Polling rate", self.rate)
        f.addRow("Lift-off distance", self.lod)
        f.addRow("", self.angle)
        f.addRow("", self.motion)
        f.addRow("", self.ripple)
        f.addRow("Sleep time (1-20)", self.sleep)
        f.addRow("", self.wake)
        f.addRow("", self.move_light)
        for wdg in (self.rate, self.lod):
            wdg.currentIndexChanged.connect(self._perf_changed)
        for wdg in (self.angle, self.motion, self.ripple, self.wake, self.move_light):
            wdg.toggled.connect(self._perf_changed)
        self.sleep.valueChanged.connect(self._perf_changed)
        return w

    def _perf_changed(self, *_):
        if self._loading:
            return
        perf, pw = self.prof["performance"], self.prof["power"]
        perf.update(polling_rate=self.rate.currentData(), lift_off_mm=self.lod.currentData(),
                    angle_snap=self.angle.isChecked(), motion_sync=self.motion.isChecked(),
                    ripple_control=self.ripple.isChecked())
        pw.update(sleep=self.sleep.value(), motion_wake=self.wake.isChecked(),
                  motion_lighting=self.move_light.isChecked())
        self.mark_dirty()

    def load_performance(self):
        perf, pw = self.prof["performance"], self.prof["power"]
        top = (self.status or {}).get("max_polling_rate")
        self.rate.clear()
        for hz in sorted(p.RATES):
            if not isinstance(top, int) or hz <= top or hz == perf["polling_rate"]:
                self.rate.addItem(f"{hz} Hz", hz)
        self.rate.setCurrentIndex(max(self.rate.findData(perf["polling_rate"]), 0))
        self.lod.setCurrentIndex(self.lod.findData(perf["lift_off_mm"]))
        self.angle.setChecked(perf["angle_snap"])
        self.motion.setChecked(perf["motion_sync"])
        self.ripple.setChecked(perf["ripple_control"])
        self.sleep.setValue(pw["sleep"])
        self.wake.setChecked(pw["motion_wake"])
        self.move_light.setChecked(pw["motion_lighting"])

    # ---------------------------------------------------------- lighting tab

    def _build_lighting(self):
        w = QWidget()
        f = QFormLayout(w)
        self.light_mode = QComboBox()
        self.light_color = ColorButton()
        self.light_bright = QSpinBox()
        self.light_bright.setRange(0, 4)
        self.light_bright.setSpecialValueText("default")
        self.light_speed = QSpinBox()
        self.light_speed.setRange(0, 3)
        self.light_speed.setSpecialValueText("default")
        f.addRow("Effect", self.light_mode)
        f.addRow("Colour (static only)", self.light_color)
        f.addRow("Brightness", self.light_bright)
        f.addRow("Speed", self.light_speed)
        note = QLabel("Most effects ignore brightness and speed. Red/green/blue need brightness, "
                      "so they default to full.")
        note.setWordWrap(True)
        f.addRow(note)
        self.light_mode.currentIndexChanged.connect(self._light_changed)
        self.light_color.changed.connect(self._light_changed)
        self.light_bright.valueChanged.connect(self._light_changed)
        self.light_speed.valueChanged.connect(self._light_changed)
        return w

    def light_modes(self) -> list[str]:
        if self.cand and self.cand.pid in p.NO_WHEEL_BINDING_PIDS:  # 026x revision
            return [m for m, v in p.LIGHT_MODES.items() if v <= 10]
        return [m for m, v in p.LIGHT_MODES.items() if v <= 7 or v == 15]

    def _light_changed(self, *_):
        if self._loading:
            return
        li = self.prof["lighting"]
        li["mode"] = self.light_mode.currentText()
        li["color"] = self.light_color.color
        for key, spin in (("brightness", self.light_bright), ("speed", self.light_speed)):
            if spin.value():
                li[key] = spin.value()
            else:
                li.pop(key, None)
        self.light_color.setEnabled(li["mode"] == "static")
        self.mark_dirty()

    def load_lighting(self):
        li = self.prof["lighting"]
        self.light_mode.clear()
        modes = self.light_modes()
        if li["mode"] not in modes:
            modes.append(li["mode"])
        self.light_mode.addItems(modes)
        self.light_mode.setCurrentText(li["mode"])
        self.light_color.set_color(li.get("color", "#ffffff"))
        self.light_color.setEnabled(li["mode"] == "static")
        self.light_bright.setValue(li.get("brightness", 0))
        self.light_speed.setValue(li.get("speed", 0))

    # ---------------------------------------------------------- macros tab

    def _build_macros(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QVBoxLayout()
        self.macro_list = QListWidget()
        self.macro_list.currentTextChanged.connect(self._select_macro)
        left.addWidget(self.macro_list)
        row = QHBoxLayout()
        for text, fn in (("Add", self.add_macro), ("Rename", self.rename_macro),
                         ("Remove", self.remove_macro)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        left.addLayout(row)
        lay.addLayout(left)
        right = QVBoxLayout()
        f = QFormLayout()
        self.macro_mode = QComboBox()
        for mode, label in (("once", "Run once"), ("repeat", "Repeat N times"),
                            ("hold", "Repeat while held"), ("toggle", "Repeat until any key")):
            self.macro_mode.addItem(label, mode)
        self.macro_repeat = QSpinBox()
        self.macro_repeat.setRange(1, 255)
        f.addRow("Mode", self.macro_mode)
        f.addRow("Repeat", self.macro_repeat)
        right.addLayout(f)
        self.macro_steps = QPlainTextEdit()
        self.macro_steps.setPlaceholderText(
            "One step per line:\n  tap:a   down:ctrl   up:ctrl   delay:100\n"
            "  tap:KEY@MS waits MS after the key; mouse-left/right/middle work as keys")
        right.addWidget(self.macro_steps)
        row = QHBoxLayout()
        self.macro_rec = MacroRecorder()
        self.macro_rec.step.connect(self.macro_steps.appendPlainText)
        row.addWidget(self.macro_rec)
        add_text = self.macro_text_btn = QPushButton("Insert text…")
        add_text.clicked.connect(self._insert_text)
        row.addWidget(add_text)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.macro_steps.clear)
        row.addWidget(clear)
        right.addLayout(row)
        self.macro_error = QLabel()
        self.macro_error.setStyleSheet("color:#d33")
        right.addWidget(self.macro_error)
        lay.addLayout(right, 1)
        self.macro_mode.currentIndexChanged.connect(self._macro_changed)
        self.macro_repeat.valueChanged.connect(self._macro_changed)
        self.macro_steps.textChanged.connect(self._macro_changed)
        return w

    def load_macros(self):
        self.macro_list.clear()
        self.macro_list.addItems(list(self.prof.get("macros", {})))
        if self.macro_list.count():
            self.macro_list.setCurrentRow(0)
        else:
            self._select_macro("")

    def _select_macro(self, name: str):
        m = self.prof.get("macros", {}).get(name)
        self._loading = True
        for wdg in (self.macro_mode, self.macro_repeat, self.macro_steps, self.macro_rec,
                    self.macro_text_btn):
            wdg.setEnabled(m is not None)
        if m:
            self.macro_mode.setCurrentIndex(self.macro_mode.findData(m.get("mode", "once")))
            self.macro_repeat.setValue(int(m.get("repeat", 1)))
            self.macro_steps.setPlainText("\n".join(m.get("steps", [])))
        else:
            self.macro_steps.clear()
        self._loading = False
        self.macro_error.setText("")

    def _macro_changed(self, *_):
        item = self.macro_list.currentItem()
        if self._loading or not item:
            return
        steps = [s.strip() for s in self.macro_steps.toPlainText().splitlines() if s.strip()]
        self.prof["macros"][item.text()] = {"mode": self.macro_mode.currentData(),
                                            "repeat": self.macro_repeat.value(), "steps": steps}
        self.macro_repeat.setEnabled(self.macro_mode.currentData() == "repeat")
        try:
            data = p.macro_events(steps)
            self.macro_error.setText(f"{len(data) // 3} events, {len(data)} of {p.MAX_MACRO_BYTES} bytes")
            self.macro_error.setStyleSheet("color:gray")
        except (p.ProtocolError, ValueError) as e:
            self.macro_error.setText(str(e))
            self.macro_error.setStyleSheet("color:#d33")
        self.mark_dirty()

    def _insert_text(self):
        text, ok = QInputDialog.getText(self, "Insert text", "Text to type:")
        if ok and text:
            try:
                self.macro_steps.appendPlainText("\n".join(p.text_steps(text)))
            except p.ProtocolError as e:
                QMessageBox.warning(self, APP_NAME, str(e))

    def add_macro(self):
        name, ok = QInputDialog.getText(self, "New macro", "Name:")
        if ok and name and name not in self.prof["macros"]:
            self.prof["macros"][name] = {"mode": "once", "repeat": 1, "steps": []}
            self.load_macros()
            self.macro_list.setCurrentRow(self.macro_list.count() - 1)
            self.mark_dirty()

    def rename_macro(self):
        item = self.macro_list.currentItem()
        if not item:
            return
        old = item.text()
        new, ok = QInputDialog.getText(self, "Rename macro", "Name:", text=old)
        if ok and new and new != old and new not in self.prof["macros"]:
            self.prof["macros"] = {new if k == old else k: v for k, v in self.prof["macros"].items()}
            for k, a in self.prof["buttons"].items():
                if a == f"macro:{old}":
                    self.prof["buttons"][k] = f"macro:{new}"
            self.load_macros()
            self.fill_button_list()
            self.mark_dirty()

    def remove_macro(self):
        item = self.macro_list.currentItem()
        if not item:
            return
        name = item.text()
        users = [k for k, a in self.prof["buttons"].items() if a == f"macro:{name}"]
        if users:
            QMessageBox.warning(self, APP_NAME, f"'{name}' is bound to {', '.join(users)}. "
                                                "Rebind those buttons first.")
            return
        del self.prof["macros"][name]
        self.load_macros()
        self.mark_dirty()

    # ---------------------------------------------------------- settings tab

    def _build_settings(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        box = QGroupBox("Profile switching")
        bl = QVBoxLayout(box)
        self.chord = QCheckBox("Hold the wheel button and press DPI+ to switch to the next profile")
        self.chord.toggled.connect(self._chord_toggled)
        bl.addWidget(self.chord)
        info = QLabel("Uses a small background service (started now and at login). A quick wheel "
                      "click is still a middle click, holding it still holds the middle button, and "
                      "DPI+ alone still raises DPI. Profiles in the order they're switched through "
                      "(untick to skip):")
        info.setWordWrap(True)
        bl.addWidget(info)
        self.cycle_list = QListWidget()
        self.cycle_list.setMaximumHeight(160)
        self.cycle_list.itemChanged.connect(self._cycle_changed)
        bl.addWidget(self.cycle_list)
        row = QHBoxLayout()
        for text, d in (("Move up", -1), ("Move down", 1)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, d=d: self._move_cycle(d))
            row.addWidget(b)
        row.addStretch()
        self.daemon_label = QLabel()
        row.addWidget(self.daemon_label)
        start = QPushButton("Start service")
        start.clicked.connect(lambda: (daemon.start_background(), QTimer.singleShot(1500, self._poll)))
        row.addWidget(start)
        bl.addLayout(row)
        lay.addWidget(box)

        box = QGroupBox("System")
        bl = QVBoxLayout(box)
        self.access_label = QLabel()
        self.access_label.setWordWrap(True)
        bl.addWidget(self.access_label)
        row = QHBoxLayout()
        udev = QPushButton("Install device permission rule (asks for your password)")
        udev.clicked.connect(self.install_udev)
        row.addWidget(udev)
        menu = QPushButton("Add to application menu")
        menu.clicked.connect(self.install_menu_entry)
        row.addWidget(menu)
        row.addStretch()
        bl.addLayout(row)
        lay.addWidget(box)
        lay.addStretch()
        return w

    def load_settings(self):
        s = profile.settings()
        self._loading = True
        self.chord.setChecked(s["chord"])
        names = profile.list_profiles()
        order = [n for n in s["cycle"] if n in names]
        rest = [n for n in names if n not in order]
        self.cycle_list.clear()
        for n in order + rest:
            item = QListWidgetItem(n)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if (n in order or not order) else Qt.CheckState.Unchecked)
            self.cycle_list.addItem(item)
        self._loading = False

    def _cycle_changed(self, *_):
        if self._loading:
            return
        s = profile.settings()
        items = [self.cycle_list.item(i) for i in range(self.cycle_list.count())]
        s["cycle"] = [i.text() for i in items if i.checkState() == Qt.CheckState.Checked]
        profile.save_settings(s)

    def _move_cycle(self, d: int):
        row = self.cycle_list.currentRow()
        if row < 0 or not 0 <= row + d < self.cycle_list.count():
            return
        item = self.cycle_list.takeItem(row)
        self.cycle_list.insertItem(row + d, item)
        self.cycle_list.setCurrentRow(row + d)
        self._cycle_changed()

    def _chord_toggled(self, on: bool):
        if self._loading:
            return
        s = profile.settings()
        s["chord"] = on
        profile.save_settings(s)
        if on:
            daemon.install_autostart()
            daemon.start_background()
        else:
            daemon.remove_autostart()
            daemon.stop()
        self.fill_button_list()
        self.statusBar().showMessage("Switching setting saved — applying buttons…")
        self.apply(only=["buttons"])

    def install_udev(self):
        rule = asset("70-sm809.rules")
        script = (f"install -m 0644 '{rule}' /etc/udev/rules.d/70-sm809.rules && "
                  "udevadm control --reload-rules && udevadm trigger --subsystem-match=hidraw")
        if subprocess.call(["pkexec", "sh", "-c", script]) == 0:
            QMessageBox.information(self, APP_NAME, "Installed. Replug the mouse or receiver if "
                                                    "it still isn't accessible.")
            QTimer.singleShot(1500, self.refresh_device)

    def install_menu_entry(self):
        apps = os.path.expanduser("~/.local/share/applications")
        icons = os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps")
        os.makedirs(apps, exist_ok=True)
        os.makedirs(icons, exist_ok=True)
        shutil.copy(asset("icon.png"), os.path.join(icons, "sm809.png"))
        exe = " ".join(f'"{a}"' if " " in a else a for a in daemon.command_line())
        with open(os.path.join(apps, "sm809.desktop"), "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=SM809 Pro\n"
                    "Comment=Configure the Solakaka SM809 Pro mouse\n"
                    f"Exec={exe} gui\nIcon=sm809\nCategories=Settings;HardwareSettings;\n")
        QMessageBox.information(self, APP_NAME, "Added “SM809 Pro” to the application menu.")

    # ---------------------------------------------------------- profiles

    def load_profile(self, name: str):
        self._loading = True
        self.name = name
        self.prof = copy.deepcopy(profile.load(name))
        self.prof.setdefault("macros", {})
        self.prof["buttons"] = {p.BUTTON_NAMES[p.button_id(k)]: v for k, v in self.prof["buttons"].items()}
        self.fill_button_list()
        self.load_dpi()
        self.load_performance()
        self.load_lighting()
        self.load_macros()
        self.load_settings()
        self._loading = False
        self.dirty = False
        self.refresh_profiles()

    def confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, f"Save changes to '{self.name}'?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save()
        return r == QMessageBox.StandardButton.Discard

    def switch_profile(self, name: str):
        if name == self.name:
            return
        if self.confirm_discard():
            self.load_profile(name)
        else:
            self.profile_combo.setCurrentText(self.name)

    def validate(self) -> str | None:
        try:
            profile.compile_sections(self.prof, lambda: 0, (self.status or {}).get("sensor_type", 17),
                                     wheel=self.wheel_editable())
        except (p.ProtocolError, ValueError, KeyError) as e:
            return str(e)
        return None

    def save(self) -> bool:
        err = self.validate()
        if err:
            QMessageBox.warning(self, APP_NAME, f"Can't save '{self.name}': {err}")
            return False
        profile.save(self.name, self.prof)
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved '{self.name}'", 3000)
        return True

    def _ask_name(self, title: str, text: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, "Profile name:", text=text)
        name = name.strip()
        if not ok or not name:
            return None
        if "/" in name or name in profile.list_profiles():
            QMessageBox.warning(self, APP_NAME, f"Can't use the name '{name}'.")
            return None
        return name

    def new_profile(self):
        if not self.confirm_discard():
            return
        name = self._ask_name("New profile")
        if name:
            profile.profile_path(name).write_text(profile.default_text())
            self.load_profile(name)

    def dup_profile(self):
        name = self._ask_name("Duplicate profile", f"{self.name}-copy")
        if name:
            profile.save(name, self.prof)
            self.load_profile(name)

    def rename_profile(self):
        if self.name == "default":
            QMessageBox.information(self, APP_NAME, "The default profile can't be renamed.")
            return
        name = self._ask_name("Rename profile", self.name)
        if name:
            profile.save(name, self.prof)
            profile.profile_path(self.name).unlink()
            if profile.active_name() == self.name:
                profile.set_active(name)
            self._rename_in_cycle(self.name, name)
            self.load_profile(name)

    def _rename_in_cycle(self, old: str, new: str | None):
        s = profile.settings()
        s["cycle"] = [new if n == old else n for n in s["cycle"] if n != old or new]
        profile.save_settings(s)

    def delete_profile(self):
        if self.name == "default":
            QMessageBox.information(self, APP_NAME, "The default profile can't be deleted.")
            return
        if QMessageBox.question(self, APP_NAME, f"Delete profile '{self.name}'?") != QMessageBox.StandardButton.Yes:
            return
        profile.profile_path(self.name).unlink()
        self._rename_in_cycle(self.name, None)
        if profile.active_name() == self.name:
            profile.set_active("default")
        self.dirty = False
        self.load_profile("default")

    # ---------------------------------------------------------- device

    def refresh_device(self):
        def work():
            cand = hidraw.best_device()
            if not cand:
                return None, None, False
            if not os.access(cand.path, os.R_OK | os.W_OK):
                return cand, None, False
            dev = hidraw.Device(cand.path, cand.pid, cand.bus)
            try:
                return cand, dev.status(), True
            finally:
                dev.close()
        self.device_label.setText("Reading the mouse…")
        self.run(work, self._device_read)

    def _device_read(self, res):
        if isinstance(res, Exception):
            self.device_label.setText(f"Mouse error: {res}")
            return
        cand, status, access = res
        self.cand, self.status = cand, status
        if not cand:
            text = "No SM809 Pro found — plug in the cable or the 2.4G receiver."
        elif not access:
            text = f"Found on {cand.path} but no permission — Settings → Install device permission rule."
        elif not status:
            text = f"Connected ({cand.mode}), no status reply."
        else:
            text = (f"<b>Connected</b> via {cand.mode} · battery {status['battery']}%"
                    f"{' (charging)' if status['charging'] == 1 else ''} · DPI stage {status['dpi_stage']}"
                    f" · {status['polling_rate']} Hz · {status['sensor']}")
            if cand.bus == 0x05:
                text += " · <i>read-only over Bluetooth</i>"
        self.device_label.setText(text)
        self.access_label.setText(
            "Device access: OK" if access else
            "The mouse needs a udev rule so it can be configured without root.")
        was = self._loading
        self._loading = True
        self.load_performance()
        self.load_lighting()
        self._loading = was
        self.fill_button_list()

    def apply(self, *, only=None):
        if self.busy:
            return
        if not only and not self.save():
            return
        prof = copy.deepcopy(self.prof)
        chord = profile.settings()["chord"]

        def work():
            cand = hidraw.best_device()
            if not cand:
                raise hidraw.DeviceError("no SM809 Pro found")
            if cand.bus == 0x05:
                raise hidraw.DeviceError("the mouse refuses settings changes over Bluetooth; "
                                         "connect the cable or the 2.4G receiver")
            dev = hidraw.Device(cand.path, cand.pid, cand.bus)
            try:
                st = dev.status() or {}
                sections = profile.compile_sections(
                    prof, dev.seq, st.get("sensor_type", 17), only,
                    wheel=cand.pid not in p.NO_WHEEL_BINDING_PIDS, chord=chord)
                for _, packets in sections:
                    for pkt in packets:
                        dev.command(pkt)
            finally:
                dev.close()
            return cand
        self.run(work, self._applied, "Applying to the mouse…")

    def _applied(self, res):
        self.busy = False
        self.apply_btn.setEnabled(True)
        if isinstance(res, Exception):
            self.statusBar().showMessage("")
            QMessageBox.warning(self, APP_NAME, f"Couldn't apply: {res}")
            return
        profile.set_active(self.name)
        self._seen_active = self.name
        self._update_title()
        self.statusBar().showMessage(f"Applied '{self.name}' via {res.mode}", 4000)

    def _poll(self):
        active = profile.active_name()
        if active != self._seen_active:  # switched by the daemon
            self._seen_active = active
            if not self.dirty and active in profile.list_profiles():
                self.load_profile(active)
            self._update_title()
        on = daemon.running()
        self.daemon_label.setText("Service: running" if on else "Service: not running")

    def closeEvent(self, ev):
        if self.confirm_discard():
            ev.accept()
        else:
            ev.ignore()


def main():
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("sm809")
    win = Window()
    win.show()
    sys.exit(app.exec())

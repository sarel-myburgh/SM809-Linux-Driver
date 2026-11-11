"""Minimal virtual mouse via /dev/uinput (stdlib only), used to emit middle clicks."""

from __future__ import annotations

import fcntl
import os
import struct

EV_SYN, EV_KEY, EV_REL = 0, 1, 2
SYN_REPORT = 0
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
REL_X, REL_Y, REL_WHEEL = 0, 1, 8

UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_DEV_SETUP = 0x405C5503  # _IOW('U', 3, struct uinput_setup), 92 bytes
BUS_VIRTUAL = 0x06


class VirtualMouse:
    def __init__(self, name: str = "SM809 middle button"):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        # Libinput only treats it as a pointer if it has buttons and relative axes.
        for ev in (EV_KEY, EV_REL):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        for btn in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, btn)
        for axis in (REL_X, REL_Y, REL_WHEEL):
            fcntl.ioctl(self.fd, UI_SET_RELBIT, axis)
        setup = struct.pack("<HHHH80sI", BUS_VIRTUAL, 0x04F3, 0x0809, 1, name.encode(), 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def _emit(self, type_: int, code: int, value: int):
        os.write(self.fd, struct.pack("llHHi", 0, 0, type_, code, value))

    def button(self, code: int, pressed: bool):
        self._emit(EV_KEY, code, int(pressed))
        self._emit(EV_SYN, SYN_REPORT, 0)

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)

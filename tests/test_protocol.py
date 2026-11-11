"""Packet tests. Expected bytes are worked out by hand from the vendor web
driver's encoders (see docs/PROTOCOL.md for the source locations)."""

import unittest

from sm809 import hidraw, profile
from sm809 import protocol as p


def counter():
    n = 0

    def seq():
        nonlocal n
        n += 1
        return n
    return seq


class Frame(unittest.TestCase):
    def test_reset_checksum(self):
        # [0F 01 01 FF] + 26×FF: (6 + 15+1+1 + 27×255) & 0xFF = 0xFC
        pkt = p.reset_packet(counter())
        self.assertEqual(pkt[:5], bytes([0x0F, 0x01, 0x01, 0xFC, 0xFF]))
        self.assertEqual(len(pkt), 31)

    def test_rate(self):
        pkt = p.rate_packet(1000, counter())
        self.assertEqual(pkt, bytes([0x04, 0x01, 0x01, 0x0D, 0x01] + [0] * 26))

    def test_rate_codes(self):
        self.assertEqual(p.RATES, {125: 8, 250: 4, 500: 2, 1000: 1, 2000: 17, 4000: 18, 8000: 20})


class Dpi(unittest.TestCase):
    def test_three_stages(self):
        x, y = p.dpi_packets([400, 800, 1600], 1, 17, counter())
        body = [0x02, 0x01, 0x02, 0x01, 0x07, 3, 0, 7, 0, 15, 0] + [0xFF] * 10 + [0xFF] * 9
        self.assertEqual(x, bytes(body[:3] + [(6 + sum(body)) & 0xFF] + body[3:]))
        self.assertEqual(y[2], 0x12)

    def test_high_dpi_little_endian(self):
        x, _ = p.dpi_packets([16000], 0, 17, counter())
        self.assertEqual(x[6:8], bytes([159 & 0xFF, 0]))  # 16000/100-1 = 159
        x, _ = p.dpi_packets([26000], 0, 149, counter())  # 50-DPI steps
        self.assertEqual(int.from_bytes(x[6:8], "little"), 26000 // 50 - 1)

    def test_rejects_bad(self):
        with self.assertRaises(p.ProtocolError):
            p.dpi_packets([450], 0, 17, counter())
        with self.assertRaises(p.ProtocolError):
            p.dpi_packets([400] * 7, 0, 17, counter())


class Buttons(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(p.action_code("left", 12), [0x10, 1, 0])
        self.assertEqual(p.action_code("back", 1), [0x10, 8, 0])
        self.assertEqual(p.action_code("forward", 1), [0x10, 16, 0])
        self.assertEqual(p.action_code("win+d", 1), [0x70, 8, 7])
        self.assertEqual(p.action_code("ctrl+c", 1), [0x70, 1, 6])
        self.assertEqual(p.action_code("2", 1), [0x70, 0, 31])
        self.assertEqual(p.action_code("my-computer", 1), [0x80, 0x94, 0x01])
        self.assertEqual(p.action_code("volume-up", 1), [0x80, 0xE9, 0x00])
        self.assertEqual(p.action_code("dpi-lock:200", 1), [0x50, 1, 0])
        self.assertEqual(p.action_code("dpi-cycle", 1), [0x40, 1, 0])
        self.assertEqual(p.action_code("fire:16:2", 1), [0x30, 16, 2])

    def test_macro_code(self):
        # Modes confirmed on hardware: 1 = while held, 2 = N times, 4 = until any key.
        modes = {"m": ("repeat", 3), "h": ("hold", 1), "o": ("once", 9), "t": ("toggle", 1)}
        self.assertEqual(p.action_code("macro:m", 5, modes), [0x90, 0x25, 3])
        self.assertEqual(p.action_code("macro:h", 16, modes), [0x90, 0x1C, 0])
        self.assertEqual(p.action_code("macro:o", 5, modes), [0x90, 0x25, 1])
        self.assertEqual(p.action_code("macro:t", 5, modes), [0x90, 0x45, 0])

    def test_named_actions(self):
        self.assertEqual(p.action_code("lock", 1), [0x70, 8, 15])  # win+l
        self.assertEqual(p.action_code("power", 1), [0x80, 0x30, 0])
        self.assertEqual(p.action_code("shift", 1), [0x70, 2, 0])  # modifier alone
        self.assertEqual(p.action_code("f24", 1), [0x70, 0, 0x73])

    def test_chord_overrides_dpi_up(self):
        prof = profile.load(str(profile.resources.files("sm809").joinpath("default.toml")))
        first, _ = profile.button_packets(prof, counter(), chord=True)
        self.assertEqual(first[10:13], bytes(p.CHORD_CODES[14]))  # slot 2 = middle
        self.assertEqual(first[13:16], bytes(p.CHORD_CODES[15]))  # slot 3 = DPI+

    def test_text(self):
        self.assertEqual(p.text_steps("aB!"), ["tap:a@15", "down:shift", "tap:b@15",
                                               "tap:1@15", "up:shift"])
        with self.assertRaises(p.ProtocolError):
            p.text_steps("é")
        prof = profile.load(str(profile.resources.files("sm809").joinpath("default.toml")))
        prof["buttons"]["side2"] = "text:Hi"
        pkts = profile.button_packets(prof, counter())
        self.assertEqual(pkts[2][0], p.CMD_MACRO_ERASE)
        self.assertEqual(pkts[3][5], 1)  # first macro goes to block 1

    def test_blocks_packed(self):
        self.assertEqual(p.allocate_macro_blocks({"a": 120, "b": 66, "c": 3}), {"a": 1, "b": 6, "c": 9})
        with self.assertRaises(p.ProtocolError):
            p.allocate_macro_blocks({"a": 300, "b": 100})

    def test_slot_order(self):
        codes = {b: [b, b, b] for b in range(1, 19)}
        a, b = p.buttons_packets(codes, counter())
        self.assertEqual((a[0], a[2], b[2]), (0x01, 0x02, 0x12))
        first = [a[i] for i in range(4, 31, 3)]
        second = [b[i] for i in range(4, 31, 3)]
        self.assertEqual(first, [12, 13, 14, 15, 16, 10, 11, 1, 2])
        self.assertEqual(second, [3, 4, 5, 6, 9, 7, 8, 17, 18])

    def test_default_profile_matches_vendor_template(self):
        prof = profile.load(str(profile.resources.files("sm809").joinpath("default.toml")))
        pkts = profile.button_packets(prof, counter())
        self.assertEqual(len(pkts), 2)  # no macros -> no erase
        self.assertEqual(pkts[0][4:7], bytes([0x10, 1, 0]))  # slot 0 = left button

    def test_sixteen_button_revision(self):
        prof = profile.load(str(profile.resources.files("sm809").joinpath("default.toml")))
        _, second = profile.button_packets(prof, counter(), wheel=False)
        self.assertEqual(second[25:31], bytes([0xFF] * 6))  # wheel slots untouched
        prof["buttons"]["wheel-up"] = "volume-up"
        with self.assertRaises(p.ProtocolError):
            profile.button_packets(prof, counter(), wheel=False)

    def test_left_required(self):
        prof = profile.load(str(profile.resources.files("sm809").joinpath("default.toml")))
        prof["buttons"]["left"] = "right"
        with self.assertRaises(p.ProtocolError):
            profile.button_packets(prof, counter())


class Macros(unittest.TestCase):
    def test_events(self):
        data = p.macro_events(["down:ctrl@30", "tap:c", "delay:300", "up:ctrl", "tap:mouse-left",
                               "move:left:5"])
        self.assertEqual(data, [
            0x20, 30, 1,             # ctrl down, wait 30
            0x30, 20, 6, 0xB0, 20, 6,  # c down/up, 20 ms each
            0x01, 0x2C, 0,           # delay 300
            0xA0, 0, 1,              # ctrl up
            0x10, 20, 1, 0x90, 20, 1,  # left click
            0x40, 0, 0xFB,           # cursor left 5
        ])

    def test_packets(self):
        data = list(range(30))
        pkts = p.macro_packets(12, data, counter())
        self.assertEqual([x[0] for x in pkts], [0x10, 0x10])
        self.assertEqual(pkts[0][2], 0x02)
        self.assertEqual(pkts[1][2], 0x12)
        self.assertEqual(pkts[0][4:6], bytes([26, 12]))  # 1 + 25 bytes, block 12
        self.assertEqual(pkts[1][4:6], bytes([6, 12]))
        self.assertEqual(pkts[0][6:31], bytes(range(25)))
        self.assertEqual(pkts[1][6:11], bytes(range(25, 30)))

    def test_erase(self):
        pkt = p.macro_erase_packet(counter())
        self.assertEqual(pkt[:5], bytes([0x11, 1, 1, (6 + 0x11 + 1 + 1 + 0xFF) & 0xFF, 0xFF]))


class Descriptor(unittest.TestCase):
    # Vendor interface descriptor captured from an SM809 Pro (04f3:026e, USB).
    DESC = bytes.fromhex(
        "05010906a1018501050719e029e71500250175019508810295067508150026ff00050719002aff00"
        "8100c0050c0901a101850219002a9c031500269c03950175108100c005010980a101850305012501"
        "150075010a81000a82000a83009503810295058101c0050a0900a1010901850419002900150025"
        "007508951f8102c0050a0900a10109018505150026ff007508951fb10209018506951fb102090185"
        "bc9507b102090185bd9587b102090185be9587b102090185bf9587b102c0")

    def test_feature_ids(self):
        self.assertEqual(hidraw.feature_report_ids(self.DESC), {5, 6, 0xBC, 0xBD, 0xBE, 0xBF})

    def test_status_parse(self):
        st = p.parse_status(bytes([1, 1, 1, 80, 0, 2, 0, 1, 17] + [0] * 22))
        self.assertEqual((st["dpi_stage"], st["polling_rate"], st["sensor"]), (2, 1000, "PAW3311"))


if __name__ == "__main__":
    unittest.main()

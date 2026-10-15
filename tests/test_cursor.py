# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cursor channel: messages, sprite encoding, rate limiting."""
import base64
import json
import struct
import zlib

import pytest

from odx11 import config, cursor
from odx11.display import Geometry

G = Geometry(239, 1080, 1568, 1088)


def test_position_is_normalised_to_the_video():
    assert cursor.position_message(239, 1080, G) == {"type": "cursor", "x": 0.0, "y": 0.0, "v": 1}
    m = cursor.position_message(239 + 784, 1080 + 544, G)
    assert (m["x"], m["y"], m["v"]) == (0.5, 0.5, 1)


def test_off_the_ipad_monitor_hides_the_cursor():
    assert cursor.position_message(100, 100, G) == {"type": "cursor", "v": 0}
    assert cursor.position_message(239 + 1568, 1500, G) == {"type": "cursor", "v": 0}   # right edge is outside


def test_premultiplied_argb_becomes_straight_rgba():
    # opaque red, half-transparent white (premultiplied 0x80808080), fully transparent
    rgba = cursor.argb_to_rgba([0xFFFF0000, 0x80808080, 0x00000000], 3, 1)
    assert rgba == bytes([255, 0, 0, 255, 255, 255, 255, 128, 0, 0, 0, 0])


def test_png_is_valid():
    data = cursor.png(2, 1, bytes([255, 0, 0, 255, 0, 255, 0, 128]))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, depth, ctype = struct.unpack(">IIBB", data[16:26])
    assert (w, h, depth, ctype) == (2, 1, 8, 6)
    idat_len = struct.unpack(">I", data[33:37])[0]
    raw = zlib.decompress(data[41:41 + idat_len])
    assert raw == b"\x00" + bytes([255, 0, 0, 255, 0, 255, 0, 128])


def test_sprite_message_normalises_size_and_hotspot_and_fits_the_demux_rule():
    m = cursor.sprite_message(24, 24, 5, 1, [0xFF000000] * 576, G)
    assert m["nw"] == round(24 / 1568, 5) and m["nh"] == round(24 / 1088, 5)
    assert (m["ax"], m["ay"]) == (round(5 / 24, 4), round(1 / 24, 4))
    assert base64.b64decode(m["png"])[:4] == b"\x89PNG"
    framed = cursor.protocol.encode_control(m)              # raises if it would demux as video
    assert len(framed) < 32768


class Clock:
    def __init__(self):
        self.now, self.timers = 0.0, []

    def call_later(self, s, fn):
        t = [self.now + s, fn, True]
        self.timers.append(t)
        return lambda: t.__setitem__(2, False)

    def advance(self, s):
        self.now += s
        for t in list(self.timers):
            if t[2] and t[0] <= self.now:
                t[2] = False
                t[1]()


def sender(rate=120):
    c, sent = Clock(), []
    cfg = config.parse({"cursor": {"max_rate_hz": rate}}).cursor
    s = cursor.CursorSender(cfg, lambda b: sent.append(json.loads(b[4:])), lambda: c.now, c.call_later)
    s.reset(G)
    return s, c, sent


def test_first_position_goes_out_immediately_and_duplicates_never():
    s, c, sent = sender()
    s.update(1000, 1500)
    s.update(1000, 1500)
    assert len(sent) == 1 and sent[0]["v"] == 1


def test_a_fast_burst_is_limited_but_the_last_position_always_arrives():
    s, c, sent = sender(rate=100)                   # one update per 10 ms
    for i in range(50):                             # a 1000 Hz mouse for 50 ms
        s.update(1000 + i, 1500)
        c.advance(0.001)
    c.advance(0.02)
    assert 5 <= len(sent) <= 7
    assert sent[-1]["x"] == round((1000 + 49 - 239) / 1568, 4)


def test_leaving_the_ipad_sends_hide_once():
    s, c, sent = sender()
    s.update(1000, 1500)
    c.advance(1)
    s.update(100, 100)
    c.advance(1)
    s.update(110, 100)
    assert [m["v"] for m in sent] == [1, 0]


def test_sprite_only_resent_when_the_image_changes():
    s, c, sent = sender()
    s.sprite(24, 24, 5, 1, [0xFF000000] * 576, serial=7)
    s.sprite(24, 24, 5, 1, [0xFF000000] * 576, serial=9)     # same arrow, another window's cursor
    s.sprite(24, 24, 5, 1, [0xFFFFFFFF] * 576, serial=8)     # a different image
    assert [m["type"] for m in sent] == ["cursorImg", "cursorImg"]


def test_reset_resends_sprite_and_position_for_a_new_stream():
    s, c, sent = sender()
    img = (24, 24, 5, 1, [0xFF000000] * 576, 7)
    s.reset(G, sprite=img, pointer=(1000, 1500))
    s.reset(G, sprite=img, pointer=(1000, 1500))
    assert [m["type"] for m in sent] == ["cursorImg", "cursor", "cursorImg", "cursor"]


def test_disabled_channel_sends_nothing():
    c, sent = Clock(), []
    cfg = config.parse({"cursor": {"enabled": False}}).cursor
    s = cursor.CursorSender(cfg, sent.append, lambda: c.now, c.call_later)
    s.reset(G, sprite=(24, 24, 5, 1, [0] * 576, 1), pointer=(1000, 1500))
    assert sent == []


# --- edges found by mutation testing ---------------------------------------------------


@pytest.mark.parametrize("argb,rgba", [
    (0xFF102030, (0x10, 0x20, 0x30, 0xFF)),         # opaque: unchanged
    (0x00102030, (0x10, 0x20, 0x30, 0x00)),         # fully transparent: no division
    (0x80402010, (127, 63, 31, 128)),               # half alpha: each channel × 255 // 128
    (0x40FF8000, (255, 255, 0, 0x40)),              # overflow clamps to 255
    (0xFEFE7F01, (255, 127, 1, 0xFE)),              # alpha 254 still un-premultiplies
    (0x4000FF00, (0, 255, 0, 0x40)),                # green clamps on its own
    (0x400000FF, (0, 0, 255, 0x40)),                # blue clamps on its own
])
def test_each_channel_is_unpremultiplied_on_its_own(argb, rgba):
    assert cursor.argb_to_rgba([argb], 1, 1) == bytes(rgba)


def test_pixels_land_at_their_own_offsets_and_extras_are_ignored():
    rgba = cursor.argb_to_rgba([0xFF010203, 0xFF040506, 0xFF070809], 2, 1)
    assert rgba == bytes([1, 2, 3, 255, 4, 5, 6, 255])
    rgba = cursor.argb_to_rgba([0xFF000001, 0xFF000002, 0xFF000003, 0xFF000004], 2, 2)
    assert rgba == bytes([0, 0, 1, 255, 0, 0, 2, 255, 0, 0, 3, 255, 0, 0, 4, 255])


def chunks(data):
    """Parse a PNG into (kind, payload), checking each chunk's length and CRC."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    out, i = [], 8
    while i < len(data):
        (n,) = struct.unpack(">I", data[i:i + 4])
        kind, payload = data[i + 4:i + 8], data[i + 8:i + 8 + n]
        (crc,) = struct.unpack(">I", data[i + 8 + n:i + 12 + n])
        assert crc == zlib.crc32(kind + payload)
        out.append((kind, payload))
        i += 12 + n
    return out


def test_png_structure_is_exact_for_several_rows():
    rgba = bytes(range(2 * 3 * 4))                   # 2 × 3 pixels, every byte distinct
    parts = chunks(cursor.png(2, 3, rgba))
    assert [k for k, _ in parts] == [b"IHDR", b"IDAT", b"IEND"]
    assert parts[0][1] == struct.pack(">IIBBBBB", 2, 3, 8, 6, 0, 0, 0)
    rows = zlib.decompress(parts[1][1])
    assert rows == b"\x00" + rgba[0:8] + b"\x00" + rgba[8:16] + b"\x00" + rgba[16:24]
    assert parts[2][1] == b""


@pytest.mark.parametrize("w,h,geom", [(0, 4, G), (4, 0, G), (-1, 4, G), (4, -1, G), (4, 4, None)])
def test_no_sprite_for_empty_sizes_or_without_a_monitor(w, h, geom):
    assert cursor.sprite_message(w, h, 0, 0, [0] * 16, geom) is None


def test_a_one_pixel_cursor_is_sent():
    assert cursor.sprite_message(1, 1, 0, 0, [0xFF000000], G) is not None


def test_sprite_message_is_exact():
    m = cursor.sprite_message(32, 16, 8, 4, [0xFF000000] * 512, G)
    assert {k: v for k, v in m.items() if k != "png"} == {
        "type": "cursorImg", "nw": round(32 / 1568, 5), "nh": round(16 / 1088, 5), "ax": 0.25, "ay": 0.25}
    assert base64.b64decode(m["png"]) == cursor.png(32, 16, cursor.argb_to_rgba([0xFF000000] * 512, 32, 16))


def test_png_size_limit_is_inclusive(monkeypatch):
    size = len(cursor.png(4, 4, cursor.argb_to_rgba([0xFF000000] * 16, 4, 4)))
    monkeypatch.setattr(cursor, "MAX_PNG", size)
    assert cursor.sprite_message(4, 4, 0, 0, [0xFF000000] * 16, G) is not None
    monkeypatch.setattr(cursor, "MAX_PNG", size - 1)
    assert cursor.sprite_message(4, 4, 0, 0, [0xFF000000] * 16, G) is None


@pytest.mark.parametrize("px,py,visible", [
    (239, 1080, True), (238, 1080, False), (239, 1079, False),
    (239 + 1567, 1080 + 1087, True), (239 + 1568, 1080, False), (239, 1080 + 1088, False),
])
def test_monitor_edges(px, py, visible):
    assert cursor.position_message(px, py, G)["v"] == (1 if visible else 0)


def test_position_rounds_to_four_places():
    m = cursor.position_message(239 + 1, 1080 + 1, G)
    assert (m["x"], m["y"]) == (round(1 / 1568, 4), round(1 / 1088, 4))


def test_before_any_monitor_the_cursor_is_hidden():
    c, sent = Clock(), []
    cfg = config.parse({}).cursor
    s = cursor.CursorSender(cfg, lambda b: sent.append(json.loads(b[4:])), lambda: c.now, c.call_later)
    s.update(1000, 1500)
    assert sent == [{"type": "cursor", "v": 0}]


def test_first_position_at_clock_zero_is_not_delayed():
    c, sent = Clock(), []
    cfg = config.parse({}).cursor
    s = cursor.CursorSender(cfg, lambda b: sent.append(json.loads(b[4:])), lambda: c.now, c.call_later)
    s.reset(G)
    s.update(1000, 1500)                             # clock() == 0.0
    assert len(sent) == 1


def test_an_update_exactly_one_interval_later_goes_out_at_once():
    s, c, sent = sender(rate=100)
    s.update(1000, 1500)
    c.now += 0.01                                    # exactly 1 / 100 s later
    s.update(1001, 1500)
    assert len(sent) == 2


def test_after_a_reset_a_new_wait_is_scheduled():
    s, c, sent = sender(rate=10)
    s.update(1000, 1500)
    s.update(1001, 1500)                             # waits for the rate limit
    s.reset(G)                                       # cancels that wait
    s.update(1002, 1500)                             # new stream: goes out at once
    s.update(1003, 1500)                             # must schedule a fresh wait …
    c.advance(0.2)
    assert [m["x"] for m in sent][-1] == round((1003 - 239) / 1568, 4)


def test_after_stop_a_new_wait_is_scheduled():
    s, c, sent = sender(rate=10)
    s.update(1000, 1500)
    s.update(1001, 1500)
    s.stop()
    s.update(1002, 1500)
    c.advance(0.2)
    assert [m["x"] for m in sent][-1] == round((1002 - 239) / 1568, 4)


def test_a_sent_position_is_not_sent_again_by_the_timer():
    s, c, sent = sender(rate=10)
    s.update(1000, 1500)
    s.update(1001, 1500)                             # pending
    c.now += 0.1
    s.update(1002, 1500)                             # interval passed: sent directly, pending dropped
    c.advance(0.2)
    assert [m["x"] for m in sent] == [round((x - 239) / 1568, 4) for x in (1000, 1002)]

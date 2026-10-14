# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cursor channel: messages, sprite encoding, rate limiting."""
import base64
import json
import struct
import zlib

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

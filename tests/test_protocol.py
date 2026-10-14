# SPDX-FileCopyrightText: 2026 vibhanshu
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import struct

import pytest

from odx11 import protocol


def framed(obj):
    p = json.dumps(obj).encode()
    return struct.pack(">I", len(p)) + p


def test_frame_is_big_endian_length_prefix():
    assert protocol.frame(b"abc") == b"\x00\x00\x00\x03abc"


@pytest.mark.parametrize("payload,expected", [
    (b'{"type":"ping"}', True),
    (b"\x00\x00\x00\x01\x67", False),                    # Annex B
    (b'{"cap":1,"snd":2}\x00\x00\x00\x01\x65', False),   # telemetry prefix + video: has NUL
    (b"[1]", False),                                      # not starting with '{'
    (b"{" + b"a" * 32767, False),                         # 32768 bytes: too long
    (b"{" + b"a" * 32766, True),                          # 32767 bytes: fits
])
def test_demux_rule(payload, expected):
    assert protocol.is_control(payload) is expected


def test_encode_control_refuses_oversize():
    with pytest.raises(protocol.ProtocolError):
        protocol.encode_control({"type": "x", "pad": "a" * 40000})


def test_encode_video_refuses_json_looking_payload():
    with pytest.raises(protocol.ProtocolError):
        protocol.encode_video(b'{"not":"video"}')


def test_welcome_advertises_pv2():
    msg = json.loads(protocol.welcome()[4:])
    assert msg == {"type": "welcome", "pv": 2, "min": 1}


def test_pong_echoes_t_unchanged():
    msg = json.loads(protocol.pong(1234.5, 99)[4:])
    assert msg == {"type": "pong", "t": 1234.5, "mt": 99}


def test_deframer_reassembles_split_and_packed_frames():
    stream = framed({"type": "hello"}) + framed({"type": "ping", "t": 1})
    d = protocol.Deframer()
    got = []
    for i in range(len(stream)):                          # one byte per read
        got += d.feed(stream[i:i + 1])
    assert [json.loads(p)["type"] for p in got] == ["hello", "ping"]
    assert [json.loads(p)["type"] for p in protocol.Deframer().feed(stream)] == ["hello", "ping"]


@pytest.mark.parametrize("length", [0, 1 << 20])
def test_deframer_rejects_bad_lengths(length):
    with pytest.raises(protocol.ProtocolError):
        protocol.Deframer().feed(struct.pack(">I", length))


@pytest.mark.parametrize("payload", [b"not json", b"[1,2]", b'{"no":"type"}', b'{"type":5}', b"\xff\xfe"])
def test_decode_control_ignores_garbage(payload):
    assert protocol.decode_control(payload) is None


def test_panel_preferred_over_deprecated_fields():
    hello = {"pixelsWide": 100, "pixelsHigh": 50, "panel": {"pixelsWide": 2360, "pixelsHigh": 1640, "scale": 2}}
    assert protocol.panel_pixels(hello) == (2360, 1640)


@pytest.mark.parametrize("panel", [
    {"pixelsWide": 2360, "pixelsHigh": 1640},                              # no scale
    {"pixelsWide": 2360, "pixelsHigh": 1640, "scale": 0},
    {"pixelsWide": 1, "pixelsHigh": 1640, "scale": 2},
    {"pixelsWide": 2360, "pixelsHigh": 1640, "scale": 2, "pointsWide": 1180},  # half the points pair
    {"pixelsWide": "2360", "pixelsHigh": 1640, "scale": 2},
    "garbage",
])
def test_malformed_panel_falls_back_whole(panel):
    hello = {"pixelsWide": 2048, "pixelsHigh": 1536, "panel": panel}
    assert protocol.panel_pixels(hello) == (2048, 1536)


def test_no_usable_size():
    assert protocol.panel_pixels({"type": "hello"}) is None


def test_video_telemetry_prefix_still_demuxes_as_video():
    framed = protocol.encode_video(b"\x00\x00\x00\x01\x65\x88", cap_ms=1000, snd_ms=1012)
    payload = framed[4:]
    assert payload.startswith(b'{"cap":1000,"snd":1012}\x00\x00\x00\x01')
    assert not protocol.is_control(payload)


def test_video_without_timestamps_has_no_prefix():
    assert protocol.encode_video(b"\x00\x00\x00\x01\x41")[4:] == b"\x00\x00\x00\x01\x41"


def test_ping_carries_health_fields():
    msg = json.loads(protocol.ping(capFps=29.5, encDrops=3, netDrops=0)[4:])
    assert msg == {"type": "ping", "capFps": 29.5, "encDrops": 3, "netDrops": 0}

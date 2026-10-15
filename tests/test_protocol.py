# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
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


# --- per-iPad decode limits ---------------------------------------------------------

AIR5 = {"type": "hello", "displayMaxFrameRate": 60,
        "videoCaps": [{"codec": "h264", "maxFrameRate": 60},
                      {"maxWidth": 2360, "maxHeight": 2360, "codec": "hevc", "maxFrameRate": 60}]}


def test_ipad_air_5_has_no_size_limit_for_h264():
    assert protocol.decode_limits(AIR5) == (None, None, 60, None)


def test_legacy_ceiling_and_videocaps_both_apply():
    hello = {"maxEncodeWide": 2048, "maxEncodeHigh": 1536,
             "videoCaps": [{"codec": "h264", "maxWidth": 1920, "maxHeight": 2000, "maxFrameRate": 30}]}
    assert protocol.decode_limits(hello) == (1920, 1536, 30, None)


def test_hevc_entries_do_not_limit_h264_and_garbage_is_ignored():
    hello = {"maxEncodeWide": "big", "videoCaps": [{"codec": "hevc", "maxWidth": 100}, "x", {"codec": "h264"}]}
    assert protocol.decode_limits(hello) == (None, None, None, None)


# --- exact bytes, edges and the iPad's hello fields (mutation testing found these unasserted)

def test_control_messages_are_compact_json():
    assert protocol.welcome() == b'\x00\x00\x00\x21{"type":"welcome","pv":2,"min":1}'
    assert protocol.pong(5, 9) == b'\x00\x00\x00\x1c{"type":"pong","t":5,"mt":9}'


def test_oversize_control_names_its_size():
    with pytest.raises(protocol.ProtocolError) as e:
        protocol.encode_control({"type": "x", "pad": "a" * 40000})
    assert str(e.value) == "control message would demux as video (40021 bytes)"


def test_json_looking_video_is_refused_by_name():
    with pytest.raises(protocol.ProtocolError) as e:
        protocol.encode_video(b'{"not":"video"}')
    assert str(e.value) == "video frame would demux as control"


def test_telemetry_prefix_is_exact_and_needs_both_times():
    au = b"\x00\x00\x00\x01\x65"
    assert protocol.encode_video(au, 12, 34) == protocol.frame(b'{"cap":12,"snd":34}' + au)
    assert protocol.encode_video(au, 12, None) == protocol.frame(au)
    assert protocol.encode_video(au, None, 34) == protocol.frame(au)


@pytest.mark.parametrize("n,ok", [(0, False), (1, True), ((1 << 20) - 1, True), (1 << 20, False)])
def test_inbound_length_limits(n, ok):
    header = struct.pack(">I", n)
    if ok:
        assert protocol.Deframer().feed(header) == []      # valid length, payload still to come
        return
    with pytest.raises(protocol.ProtocolError) as e:
        protocol.Deframer().feed(header)
    assert str(e.value) == f"bad inbound frame length {n}"


GOOD_PANEL = {"pixelsWide": 2360, "pixelsHigh": 1640, "scale": 2, "pointsWide": 1180, "pointsHigh": 820}


@pytest.mark.parametrize("change,usable", [
    ({}, True),
    ({"pixelsWide": 2, "pixelsHigh": 2, "pointsWide": 2, "pointsHigh": 2}, True),     # smallest valid
    ({"pixelsWide": 1}, False),
    ({"pixelsHigh": 1}, False),
    ({"pointsWide": 1}, False),
    ({"pointsHigh": 1}, False),
    ({"pointsHigh": None}, False),           # only one of the two points fields
    ({"pointsWide": None, "pointsHigh": None}, True),   # both absent is allowed
    ({"pointsWide": True}, False),           # a bool is not a size
    ({"scale": 0}, False),
    ({"scale": -2}, False),
    ({"scale": float("inf")}, False),
    ({"scale": True}, False),
    ({"scale": "2"}, False),
    ({"scale": 0.5}, True),
])
def test_each_panel_field_is_checked(change, usable):
    panel = {k: v for k, v in {**GOOD_PANEL, **change}.items() if v is not None}
    hello = {"panel": panel, "pixelsWide": 100, "pixelsHigh": 80}
    expected = (panel["pixelsWide"], panel["pixelsHigh"]) if usable else (100, 80)
    assert protocol.panel_pixels(hello) == expected


@pytest.mark.parametrize("w,h,result", [(2, 2, (2, 2)), (1, 80, None), (100, 1, None), (True, 80, None)])
def test_deprecated_size_fields_edges(w, h, result):
    assert protocol.panel_pixels({"pixelsWide": w, "pixelsHigh": h}) == result


def h264(**kw):
    return {"codec": "h264", **kw}


def test_no_limits_when_nothing_is_said():
    assert protocol.decode_limits({}) == (None, None, None, None)


def test_legacy_ceiling_alone_and_bad_values_ignored():
    assert protocol.decode_limits({"maxEncodeWide": 1920, "maxEncodeHigh": 1080}) == (1920, 1080, None, None)
    assert protocol.decode_limits({"maxEncodeWide": 0, "maxEncodeHigh": -5}) == (None, None, None, None)
    assert protocol.decode_limits({"maxEncodeWide": True, "maxEncodeHigh": "1080"}) == (None, None, None, None)


def test_videocaps_reports_every_limit():
    hello = {"videoCaps": [h264(maxWidth=3840, maxHeight=2160, maxFrameRate=60, maxPixelsPerSecond=497664000)]}
    assert protocol.decode_limits(hello) == (3840, 2160, 60, 497664000)


@pytest.mark.parametrize("legacy,cap,expected", [
    ((1920, 1080), (3840, 2160), (1920, 1080)),       # legacy tighter
    ((3840, 2160), (1920, 1080), (1920, 1080)),       # videoCaps tighter
    ((1920, 2160), (3840, 1080), (1920, 1080)),       # each tighter in one direction
    ((None, None), (1920, 1080), (1920, 1080)),       # only videoCaps
])
def test_the_tighter_of_legacy_and_videocaps_wins(legacy, cap, expected):
    hello = {"videoCaps": [h264(maxWidth=cap[0], maxHeight=cap[1])]}
    if legacy[0]:
        hello.update(maxEncodeWide=legacy[0], maxEncodeHigh=legacy[1])
    assert protocol.decode_limits(hello)[:2] == expected


def test_videocaps_without_size_keeps_the_legacy_ceiling():
    hello = {"maxEncodeWide": 1920, "maxEncodeHigh": 1080, "videoCaps": [h264(maxFrameRate=30)]}
    assert protocol.decode_limits(hello) == (1920, 1080, 30, None)


def test_the_roomiest_entry_by_area_is_used():
    wide_strip = h264(maxWidth=4000, maxHeight=100, maxFrameRate=30)        # area 400 000, ratio 40
    square = h264(maxWidth=1000, maxHeight=1000, maxFrameRate=60)          # area 1 000 000, ratio 1
    assert protocol.decode_limits({"videoCaps": [wide_strip, square]})[2] == 60
    assert protocol.decode_limits({"videoCaps": [square, wide_strip]})[2] == 60


def test_a_missing_dimension_counts_as_unlimited_when_choosing():
    sized = h264(maxWidth=3840, maxHeight=2160, maxFrameRate=30)
    open_height = h264(maxWidth=1000, maxFrameRate=120)                    # 1000 × unlimited is roomier
    open_width = h264(maxHeight=1000, maxFrameRate=90)
    assert protocol.decode_limits({"videoCaps": [sized, open_height]}) == (1000, None, 120, None)
    assert protocol.decode_limits({"videoCaps": [sized, open_width]}) == (None, 1000, 90, None)


def test_videocaps_must_be_a_list_of_dicts():
    assert protocol.decode_limits({"videoCaps": {"codec": "h264", "maxWidth": 10}}) == (None, None, None, None)
    assert protocol.decode_limits({"videoCaps": ["h264", None]}) == (None, None, None, None)


def test_any_positive_limit_counts_even_one():
    assert protocol.decode_limits({"videoCaps": [h264(maxFrameRate=1, maxPixelsPerSecond=0.5)]}) == (None, None, 1, 0.5)

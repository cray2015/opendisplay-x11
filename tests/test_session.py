# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest

from odx11 import config
from odx11.daemon import Session
from odx11.display import Geometry


class FakeDisplay:
    def __init__(self):
        self.attached = []
        self.current = None

    def attach(self, w, h, device_cfg):
        self.attached.append((w, h, device_cfg.side))
        self.current = Geometry(1920, 0, 1568, 1088) if w >= h else Geometry(1920, 0, 1088, 1568)
        return self.current

    def geometry(self):
        return self.current


class FakeInjector:
    def __init__(self):
        self.geometry, self.msgs, self.released = None, [], 0

    def set_geometry(self, g):
        self.geometry = g

    def handle(self, msg):
        self.msgs.append(msg)

    def release_all(self):
        self.released += 1


class FakePipeline:
    def __init__(self, geom, on_au, on_error):
        self.geom, self.on_au, self.on_error = geom, on_au, on_error
        self.started = self.stopped = False
        self.idr_requests = 0

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def force_idr(self):
        self.idr_requests += 1


HELLO = {"type": "hello", "id": "ipad-1", "pv": 3, "pixelsWide": 2360, "pixelsHigh": 1640,
         "panel": {"pixelsWide": 2360, "pixelsHigh": 1640, "scale": 2}}


def make(raw=None):
    sent, pipes = [], []
    cfg = config.parse(raw or {})
    s = Session(cfg, sent.append, FakeDisplay(), FakeInjector(),
                lambda g, a, e: pipes.append(FakePipeline(g, a, e)) or pipes[-1], clock=lambda: 777)
    return s, sent, pipes


def feed(s, msg):
    s.on_payload(json.dumps(msg).encode())


def decoded(sent):
    return [json.loads(f[4:]) for f in sent]


def test_hello_gets_welcome_then_display_and_stream():
    s, sent, pipes = make({"devices": {"ipad-1": {"side": "left"}}})
    feed(s, HELLO)
    assert decoded(sent) == [{"type": "welcome", "pv": 2, "min": 1}]
    assert s.display.attached == [(2360, 1640, "left")]
    assert s.injector.geometry == Geometry(1920, 0, 1568, 1088)
    assert len(pipes) == 1 and pipes[0].started


def test_every_hello_gets_welcome_but_same_panel_does_not_rebuild():
    s, sent, pipes = make()
    feed(s, HELLO)
    feed(s, HELLO)
    assert [m["type"] for m in decoded(sent)] == ["welcome", "welcome"]
    assert len(pipes) == 1 and not pipes[0].stopped


def test_rotation_rebuilds_with_new_geometry():
    s, sent, pipes = make()
    feed(s, HELLO)
    feed(s, {**HELLO, "panel": {"pixelsWide": 1640, "pixelsHigh": 2360, "scale": 2}})
    assert pipes[0].stopped and pipes[1].started
    assert s.injector.geometry == Geometry(1920, 0, 1088, 1568)


def test_ping_gets_pong_echoing_t():
    s, sent, _ = make()
    feed(s, {"type": "ping", "t": 1728000000123.25})
    assert decoded(sent) == [{"type": "pong", "t": 1728000000123.25, "mt": 777}]


def test_kf_forces_idr():                       # AC 13, unit half
    s, _, pipes = make()
    feed(s, HELLO)
    feed(s, {"type": "kf"})
    assert pipes[0].idr_requests == 1


def test_kf_before_stream_is_harmless():
    s, _, _ = make()
    feed(s, {"type": "kf"})


def test_touch_and_scroll_go_to_injector():
    s, _, _ = make()
    feed(s, HELLO)
    feed(s, {"type": "touch", "phase": "began", "x": 0.5, "y": 0.5})
    feed(s, {"type": "scroll", "dx": 0, "dy": 40})
    assert [m["type"] for m in s.injector.msgs] == ["touch", "scroll"]


def test_unknown_types_ignored_and_logged_once(caplog):
    s, sent, _ = make()
    caplog.set_level("INFO")
    for _ in range(3):
        feed(s, {"type": "pencil", "x": 0, "y": 0})
    assert sent == [] and s.ended is None
    assert sum("pencil" in r.message for r in caplog.records) == 1


def test_garbage_payload_is_not_fatal():
    s, _, _ = make()
    s.on_payload(b"not json")
    assert s.ended is None


def test_sleeping_and_closing_end_the_session():
    for t in ("sleeping", "closing"):
        s, _, _ = make()
        ended = []
        s.on_end = lambda: ended.append(True)
        feed(s, {"type": t})
        assert s.ended and ended == [True]


def test_video_goes_out_framed_as_video():
    s, sent, pipes = make()
    feed(s, HELLO)
    sent.clear()
    pipes[0].on_au(b"\x00\x00\x00\x01\x65\x88", True)
    assert sent == [b"\x00\x00\x00\x06\x00\x00\x00\x01\x65\x88"]


def test_stop_releases_held_button_and_stops_stream():
    s, _, pipes = make()
    feed(s, HELLO)
    s.stop()
    assert pipes[0].stopped and s.injector.released == 1


MOVED = Geometry(0, 1080, 1568, 1088)        # dragged below the main monitor in Cinnamon


def test_moved_output_restarts_capture_and_remaps_input():
    s, _, pipes = make()
    feed(s, HELLO)
    s.display.current = MOVED
    s.on_display_change()
    assert pipes[0].stopped and pipes[1].started and pipes[1].geom == MOVED
    assert s.injector.geometry == MOVED and s.ended is None


def test_unrelated_randr_event_changes_nothing():
    s, _, pipes = make()
    feed(s, HELLO)
    s.on_display_change()
    assert len(pipes) == 1 and not pipes[0].stopped


def test_capture_error_from_a_move_rebuilds_instead_of_ending():
    s, _, pipes = make()
    feed(s, HELLO)
    s.display.current = MOVED                 # error arrives before the RandR event
    pipes[0].on_error("Internal data stream error.")
    assert s.ended is None and pipes[1].geom == MOVED


def test_capture_error_without_a_move_ends_session():
    s, _, pipes = make()
    feed(s, HELLO)
    pipes[0].on_error("Internal data stream error.")
    assert "pipeline error" in s.ended


def test_late_error_from_replaced_pipeline_is_ignored():
    s, _, pipes = make()
    feed(s, HELLO)
    s.display.current = MOVED
    pipes[0].on_error("Internal data stream error.")     # ximagesrc's error: rebuilds
    pipes[0].on_error("Internal data stream error.")     # queue's echo of it: stale
    assert s.ended is None and len(pipes) == 2


def test_disabled_output_ends_session():
    s, _, _ = make()
    feed(s, HELLO)
    s.display.current = None
    s.on_display_change()
    assert s.ended == "virtual monitor was disabled"


def test_failed_video_send_ends_session_and_stops_stream():
    def boom(_):
        raise BrokenPipeError("gone")
    s, _, pipes = make()
    feed(s, HELLO)
    s.send = boom
    with pytest.raises(OSError):
        pipes[0].on_au(b"\x00\x00\x00\x01\x41", False)
    assert s.ended.startswith("send failed")


def test_video_with_capture_time_gets_telemetry_prefix():
    s, sent, pipes = make()
    feed(s, HELLO)
    sent.clear()
    pipes[0].on_au(b"\x00\x00\x00\x01\x41", False, 700)
    assert sent[0][4:].startswith(b'{"cap":700,"snd":777}')


def test_ping_includes_pipeline_health():
    s, sent, pipes = make()
    feed(s, HELLO)
    pipes[0].health = lambda: {"capFps": 30.0, "encDrops": 0, "netDrops": 0}
    sent.clear()
    s.ping()
    assert json.loads(sent[0][4:]) == {"type": "ping", "capFps": 30.0, "encDrops": 0, "netDrops": 0}

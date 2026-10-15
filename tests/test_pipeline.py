# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""The capture → encode pipeline with GStreamer replaced by a small fake: the pipeline
description, the GPU/CPU choice and fallback, the frame gate, health counters, capture times,
forcing an IDR and handing access units on. Real GStreamer is used only by the last test, where
it is installed."""
import importlib.util
import subprocess
import sys
from types import SimpleNamespace

import pytest

from odx11 import config, pipeline
from odx11.display import Geometry

S4 = b"\x00\x00\x00\x01"
SPS, PPS, IDR, P = b"\x67\x64\x00\x28\xac", b"\x68\xee\x3c\x80", b"\x65\x88\x84\x00\x10", b"\x41\x9a\x02\x03"


# --- a fake GStreamer ------------------------------------------------------------------

class Pad:
    def __init__(self):
        self.probes = []

    def add_probe(self, kind, fn):
        self.probes.append((kind, fn))


class Element:
    def __init__(self):
        self.pads, self.signals, self.events = {}, {}, []

    def get_static_pad(self, name):
        return self.pads.setdefault(name, Pad())

    def connect(self, signal, fn):
        self.signals[signal] = fn

    def send_event(self, event):
        self.events.append(event)


class Bus:
    def __init__(self):
        self.watching, self.handlers = False, {}

    def add_signal_watch(self):
        self.watching = True

    def remove_signal_watch(self):
        self.watching = False

    def connect(self, signal, fn):
        self.handlers[signal] = fn


class Pipe:
    def __init__(self, gst, desc):
        self.gst, self.desc, self.states = gst, desc, []
        self.elements = {n: Element() for n in ("out", "q", "conv")}
        self.bus = Bus()
        self.clock, self.base_time = None, 0

    def get_by_name(self, name):
        return self.elements[name]

    def set_state(self, st):
        self.states.append(st)
        failing = st == FakeGst.State.PLAYING and any(f in self.desc for f in self.gst.fail_with)
        return FakeGst.StateChangeReturn.FAILURE if failing else FakeGst.StateChangeReturn.SUCCESS

    def get_bus(self):
        return self.bus

    def get_clock(self):
        return self.clock

    def get_base_time(self):
        return self.base_time


class FakeGst:
    class State:
        NULL, PAUSED, PLAYING = "null", "paused", "playing"

    class StateChangeReturn:
        SUCCESS, FAILURE = "success", "failure"

    class PadProbeType:
        BUFFER = "buffer"

    class PadProbeReturn:
        OK, DROP = "ok", "drop"

    class FlowReturn:
        OK, EOS, ERROR = "flow-ok", "flow-eos", "flow-error"

    class MapFlags:
        READ = "read"

    CLOCK_TIME_NONE = 2 ** 64 - 1

    def __init__(self, factories=()):
        self.pipes, self.fail_with, self.factories = [], [], set(factories)
        self.ElementFactory = SimpleNamespace(find=lambda name: name if name in self.factories else None)

    def parse_launch(self, desc):
        self.pipes.append(Pipe(self, desc))
        return self.pipes[-1]


class FakeGstVideo:
    @staticmethod
    def video_event_new_upstream_force_key_unit(ts, all_headers, count):
        return ("force-key-unit", ts, all_headers, count)


@pytest.fixture
def gst(monkeypatch):
    g = FakeGst(factories={"vaapipostproc"})
    monkeypatch.setattr(pipeline, "Gst", g)
    monkeypatch.setattr(pipeline, "GstVideo", FakeGstVideo)
    return g


@pytest.fixture
def clock(monkeypatch):
    c = SimpleNamespace(mono=100.0, wall=1_700_000_000.0)
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: c.mono)
    monkeypatch.setattr(pipeline.time, "time", lambda: c.wall)
    return c


G = Geometry(239, 1080, 1568, 1088)


def video(**raw):
    return config.parse({"video": raw}).video


def make(gst, **raw):
    got = {"au": [], "errors": []}
    p = pipeline.Pipeline(G, video(**raw), ":0", lambda au, idr, cap: got["au"].append((au, idr, cap)),
                          got["errors"].append)
    p.got = got
    return p


# --- import and start-up -----------------------------------------------------------------

def test_importing_the_module_starts_nothing():
    code = ("import sys, subprocess; ran = []; subprocess.run = lambda *a, **k: ran.append(a); "
            "import odx11.pipeline as p; "
            "print(p.Gst is None, 'gi.repository.Gst' in sys.modules, ran == [])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.split() == ["True", "False", "True"]


def test_init_runs_once(monkeypatch):
    refreshed = []
    monkeypatch.setattr(pipeline, "_refresh_registry", lambda: refreshed.append(1))
    monkeypatch.setattr(pipeline, "Gst", "already started")
    pipeline.init()
    assert refreshed == [] and pipeline.Gst == "already started"


@pytest.mark.parametrize("answer", [SimpleNamespace(stderr="(gst-plugin-scanner:1): CRITICAL ...\n\n"),
                                    FileNotFoundError("gst-inspect-1.0"),
                                    subprocess.TimeoutExpired("gst-inspect-1.0", 60)])
def test_the_registry_refresh_keeps_scanner_noise_in_the_debug_log(monkeypatch, caplog, answer):
    calls = []
    def run(cmd, **kw):
        calls.append((cmd, kw))
        if isinstance(answer, BaseException):
            raise answer
        return answer
    monkeypatch.setattr(pipeline.subprocess, "run", run)
    caplog.set_level("DEBUG", logger="odx11.pipeline")
    pipeline._refresh_registry()
    assert calls == [(["gst-inspect-1.0", "vaapih264enc"], {"capture_output": True, "text": True, "timeout": 60})]
    debug = [r.getMessage() for r in caplog.records if r.levelname == "DEBUG"]
    if isinstance(answer, BaseException):
        assert debug == [f"registry refresh skipped: {answer}"]
    else:
        assert debug == ["gst-plugin-scanner: (gst-plugin-scanner:1): CRITICAL ..."]
    assert not [r for r in caplog.records if r.levelno > 10]


# --- the description and the GPU/CPU choice ----------------------------------------------

def test_cpu_description_is_exact():
    d = pipeline.describe(G, video(preset="custom", fps=50, qp=24, keyframe_period=600, convert_threads=3),
                          ":1", gpu=False)
    assert d == (
        "ximagesrc display-name=:1 startx=239 starty=1080 endx=1806 endy=2167 use-damage=false "
        "show-pointer=false ! video/x-raw,framerate=50/1 "
        "! queue name=q leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
        "! videoconvert name=conv n-threads=3 ! video/x-raw,format=NV12 "
        "! vaapih264enc tune=low-power rate-control=cqp init-qp=24 max-bframes=0 keyframe-period=600 "
        "! h264parse config-interval=-1 "
        "! video/x-h264,stream-format=byte-stream,alignment=au "
        "! appsink name=out sync=false emit-signals=true max-buffers=0 drop=false")


def test_the_description_converts_on_the_cpu_unless_told_otherwise():
    assert "videoconvert name=conv" in pipeline.describe(G, video(), ":0")


def test_gpu_description_converts_on_the_gpu():
    d = pipeline.describe(G, video(), ":0", gpu=True)
    assert "! vaapipostproc name=conv ! video/x-raw(memory:VASurface),format=NV12 ! vaapih264enc" in d
    assert "videoconvert" not in d


def test_preset_values_and_the_ipads_frame_rate_cap_apply():
    v = video(preset="best_quality", fps=24, qp=40, show_pointer=True)
    d = pipeline.describe(G, v, ":0")
    assert "framerate=60/1" in d and "init-qp=22" in d and "show-pointer=true" in d     # the preset's, not 24/40
    assert "framerate=30/1" in pipeline.describe(G, v, ":0", fps=30)                      # capped for this iPad


@pytest.mark.parametrize("convert,registered,gpu", [
    ("auto", True, True), ("auto", False, False), ("gpu", False, True), ("cpu", True, False)])
def test_where_frames_are_processed(gst, convert, registered, gpu):
    gst.factories = {"vaapipostproc"} if registered else set()
    assert pipeline.gpu_convert_available() is registered
    assert pipeline.use_gpu_convert(video(convert=convert)) is gpu


# --- building and starting ----------------------------------------------------------------

def test_building_wires_the_probes_the_sink_and_the_bus(gst, caplog):
    caplog.set_level("DEBUG", logger="odx11.pipeline")
    p = make(gst)
    pipe = gst.pipes[0]
    assert [r.getMessage() for r in caplog.records] == [f"pipeline: {pipe.desc}"]
    assert pipe.elements["out"].signals["new-sample"] == p._on_sample
    assert pipe.desc == pipeline.describe(G, video(), ":0", gpu=True)
    assert "new-sample" in pipe.elements["out"].signals
    assert [k for k, _ in pipe.elements["q"].pads["sink"].probes] == ["buffer"]          # capture counter
    assert pipe.elements["conv"].pads["sink"].probes == [("buffer", p._gate)]            # the frame gate
    assert pipe.bus.watching and "message::error" in pipe.bus.handlers


def test_start_pause_resume_stop(gst, caplog):
    p = make(gst, convert="cpu")
    caplog.set_level("INFO")
    p.start()
    p.pause()
    p.resume()
    p.stop()
    assert gst.pipes[0].states == ["playing", "paused", "playing", "null"]
    assert not gst.pipes[0].bus.watching
    assert [r.getMessage() for r in caplog.records] == ["colour conversion: software (videoconvert)"]


def test_auto_falls_back_to_the_cpu_when_the_gpu_cannot_start(gst, caplog):
    gst.fail_with = ["vaapipostproc"]
    p = make(gst)
    caplog.set_level("INFO")
    p.start()
    assert p.gpu is False and len(gst.pipes) == 2
    assert gst.pipes[0].states == ["playing", "null"] and gst.pipes[1].states == ["playing"]
    assert "videoconvert" in gst.pipes[1].desc
    assert [r.getMessage() for r in caplog.records] == [
        "GPU colour conversion failed to start; converting in software", "colour conversion: software (videoconvert)"]


@pytest.mark.parametrize("convert,fail", [("gpu", "vaapipostproc"), ("cpu", "videoconvert"), ("auto", "vaapih264enc")])
def test_no_fallback_when_it_cannot_help(gst, convert, fail):
    gst.fail_with = [fail]
    p = make(gst, convert=convert)
    with pytest.raises(RuntimeError) as e:
        p.start()
    assert str(e.value) == "pipeline failed to start"


def test_gpu_start_is_logged(gst, caplog):
    caplog.set_level("INFO")
    make(gst).start()
    assert [r.getMessage() for r in caplog.records] == ["colour conversion: GPU (vaapipostproc)"]


# --- the frame gate and health ------------------------------------------------------------

def test_frames_pass_only_while_the_gate_is_open(gst, clock):
    p = make(gst)
    assert p._gate(None, None) == "drop"
    p.open_gate(0.15)
    assert p._gate(None, None) == "ok"
    clock.mono += 0.149
    assert p._gate(None, None) == "ok"
    clock.mono += 0.002
    assert p._gate(None, None) == "drop"
    assert (p._encoded, p._gated) == (2, 2)


def test_the_gate_closes_exactly_when_its_time_is_up(gst, clock):
    p = make(gst)
    p.open_gate(0.25)
    clock.mono += 0.25
    assert p._gate(None, None) == "drop"


def test_a_shorter_wake_never_shortens_the_gate(gst, clock):
    p = make(gst)
    p.open_gate(0.5)
    p.open_gate(0.1)
    clock.mono += 0.3
    assert p._gate(None, None) == "ok"


def test_a_zero_tail_sends_every_frame(gst):
    p = make(gst, preset="custom", change_tail_ms=0)
    assert all(p._gate(None, None) == "ok" for _ in range(3))


def test_health_reports_rate_and_the_queues_drops(gst, clock):
    p = make(gst)
    count = gst.pipes[0].elements["q"].pads["sink"].probes[0][1]
    for _ in range(10):
        assert count(None, None) == "ok"                     # 10 frames captured
    p.open_gate(1)
    for _ in range(6):
        p._gate(None, None)                                  # 6 converted
    clock.mono += 10
    p._gate(None, None)                                      # 1 gated, gate closed by now
    p.frames = 30
    assert p.health() == {"capFps": 3.0, "encDrops": 2, "netDrops": 0}      # 10 - 6 - 1 - 1 in flight
    assert p.health() == {"capFps": 0.0, "encDrops": 2, "netDrops": 0}      # no time passed since
    p.frames = 40
    clock.mono += 3
    assert p.health()["capFps"] == 3.3                       # 10 frames in 3 s, one decimal


def test_drops_never_go_negative(gst):
    assert make(gst).health()["encDrops"] == 0


# --- capture time, IDR, samples, errors ---------------------------------------------------

def test_capture_time_is_wall_clock_minus_the_frames_age(gst, clock):
    p = make(gst)
    pipe = gst.pipes[0]
    buf = SimpleNamespace(pts=5_000_000_000)                 # captured at running time 5 s
    assert p._capture_ms(buf) is None                        # no clock yet
    pipe.clock = SimpleNamespace(get_time=lambda: 12_030_000_000)
    pipe.base_time = 7_000_000_000                           # running time now: 5.03 s
    assert p._capture_ms(buf) == round(clock.wall * 1000 - 30)
    pipe.clock = SimpleNamespace(get_time=lambda: 1_012_000_000_000)          # 1000 s later
    assert p._capture_ms(buf) == round(clock.wall * 1000 - 1_000_000)
    assert p._capture_ms(SimpleNamespace(pts=FakeGst.CLOCK_TIME_NONE)) is None


def test_kf_asks_upstream_for_a_key_unit_with_headers(gst):
    p = make(gst)
    p.force_idr()
    assert gst.pipes[0].elements["out"].events == [("force-key-unit", FakeGst.CLOCK_TIME_NONE, True, 0)]


class Buffer:
    def __init__(self, data, mappable=True, pts=FakeGst.CLOCK_TIME_NONE):
        self.data, self.mappable, self.pts, self.unmapped = data, mappable, pts, 0

    def map(self, flags):
        assert flags == "read"
        self.info = SimpleNamespace(data=memoryview(self.data))
        return self.mappable, self.info

    def unmap(self, info):
        assert info is self.info                             # unmap what was mapped
        self.unmapped += 1


def sink_with(buf):
    sample = None if buf is None else SimpleNamespace(get_buffer=lambda: buf)
    return SimpleNamespace(emit=lambda name: sample if name == "pull-sample" else None)


def test_each_sample_becomes_one_normalised_access_unit(gst):
    p = make(gst)
    buf = Buffer(b"\x00\x00\x01" + SPS + b"\x00\x00\x01" + PPS + b"\x00\x00\x01" + IDR)
    assert p._on_sample(sink_with(buf)) == "flow-ok"
    assert p.got["au"] == [(S4 + SPS + S4 + PPS + S4 + IDR, True, None)]
    assert p.frames == 1 and buf.unmapped == 1
    assert p._on_sample(sink_with(Buffer(S4 + P))) == "flow-ok"
    assert p.got["au"][-1] == (S4 + P, False, None) and p.frames == 2


def test_each_access_unit_carries_its_capture_time(gst, clock):
    p = make(gst)
    pipe = gst.pipes[0]
    pipe.clock = SimpleNamespace(get_time=lambda: 2_000_000_000)
    p._on_sample(sink_with(Buffer(S4 + SPS + S4 + PPS + S4 + IDR, pts=1_990_000_000)))      # 10 ms old
    assert p.got["au"][0][2] == round(clock.wall * 1000 - 10)


def test_an_empty_access_unit_is_not_sent(gst):
    p = make(gst)
    assert p._on_sample(sink_with(Buffer(S4 + b"\x09\x10"))) == "flow-ok"     # AUD only
    assert p.got["au"] == [] and p.frames == 0


def test_no_sample_or_an_unreadable_buffer(gst):
    p = make(gst)
    assert p._on_sample(sink_with(None)) == "flow-eos"
    buf = Buffer(b"", mappable=False)
    assert p._on_sample(sink_with(buf)) == "flow-error" and buf.unmapped == 0


def test_a_dead_socket_ends_the_stream(gst):
    def gone(au, idr, cap):
        raise BrokenPipeError()
    p = pipeline.Pipeline(G, video(), ":0", gone, lambda m: None)
    assert p._on_sample(sink_with(Buffer(S4 + SPS + S4 + PPS + S4 + IDR))) == "flow-eos"
    assert p.frames == 0


def test_bad_encoder_output_still_unmaps_the_buffer(gst):
    p = make(gst)
    buf = Buffer(S4 + IDR)                                   # an IDR before any SPS/PPS
    with pytest.raises(ValueError):
        p._on_sample(sink_with(buf))
    assert buf.unmapped == 1


def test_bus_errors_are_logged_and_passed_on(gst, caplog):
    p = make(gst)
    msg = SimpleNamespace(parse_error=lambda: (SimpleNamespace(message="Could not open display"), "ximagesrc.c:42"))
    gst.pipes[0].bus.handlers["message::error"](None, msg)
    assert p.got["errors"] == ["Could not open display"]
    assert [r.getMessage() for r in caplog.records] == ["pipeline error: Could not open display (ximagesrc.c:42)"]


# --- real GStreamer, where installed ------------------------------------------------------

@pytest.mark.skipif(importlib.util.find_spec("gi") is None, reason="needs PyGObject")
def test_init_starts_real_gstreamer_once(monkeypatch):
    try:
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstVideo", "1.0")
    except (ImportError, ValueError):
        pytest.skip("GStreamer introspection data is not installed")
    refreshed = []
    monkeypatch.setattr(pipeline, "_refresh_registry", lambda: refreshed.append(1))
    monkeypatch.setattr(pipeline, "Gst", None)
    monkeypatch.setattr(pipeline, "GstVideo", None)
    pipeline.init()
    pipeline.init()
    assert refreshed == [1]
    assert pipeline.Gst.is_initialized() and pipeline.GstVideo is not None

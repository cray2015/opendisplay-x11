# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture → encode pipeline (PROJECT_SPEC.md §8.3).

Backpressure: the appsink callback sends synchronously, so a slow socket
blocks the streaming thread and the leaky queue upstream drops *raw* frames.
Encoded frames are never dropped. `pipeline.py` never edits bytes; every
access unit goes through `annexb.Normaliser`.
"""
import logging
import subprocess
import time

log = logging.getLogger(__name__)


def _refresh_registry():
    """Rescan GStreamer plugins in a child whose stderr we keep, before Gst.init.

    Forcing the connector touches /dev/dri/card1, which the VA plugins list as a
    registry dependency, so the next GStreamer start rescans — and the vaapi plugin
    prints a harmless `CRITICAL ... _dma_fmt_to_dma_drm_fmts` while being scanned.
    Doing the rescan here sends that to the debug log; Gst.init then finds a fresh
    registry. Real plugin problems still surface as a missing element at parse time."""
    try:
        r = subprocess.run(["gst-inspect-1.0", "vaapih264enc"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.debug("registry refresh skipped: %s", e)
        return
    for line in r.stderr.splitlines():
        if line.strip():
            log.debug("gst-plugin-scanner: %s", line.strip())


_refresh_registry()

import gi  # noqa: E402
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstVideo  # noqa: E402

from . import annexb  # noqa: E402

Gst.init(None)


def describe(geom, video, display_name: str, fps: int | None = None) -> str:
    """gst-launch description for capturing `geom` of the X screen. `video` is [video] config;
    `fps` overrides video.fps (the session caps it at the iPad's maximum)."""
    x, y, w, h = geom
    fps = fps or video.effective("fps")
    return (
        f"ximagesrc display-name={display_name} startx={x} starty={y} "
        f"endx={x + w - 1} endy={y + h - 1} use-damage=false "
        f"show-pointer={'true' if video.show_pointer else 'false'} "
        f"! video/x-raw,framerate={fps}/1 "
        "! queue name=q leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
        f"! videoconvert name=conv n-threads={video.convert_threads} ! video/x-raw,format=NV12 "
        # vaapih264enc, not vah264lpenc: the latter answers force-key-unit with a non-IDR I-frame.
        f"! vaapih264enc tune=low-power rate-control=cqp init-qp={video.effective("qp")} max-bframes=0 "
        f"keyframe-period={video.keyframe_period} "
        "! h264parse config-interval=-1 "
        "! video/x-h264,stream-format=byte-stream,alignment=au "
        "! appsink name=out sync=false emit-signals=true max-buffers=0 drop=false"
    )


class Pipeline:
    """on_au(bytes, is_idr, cap_ms) runs on the GStreamer streaming thread and may block;
    raising OSError stops the stream. cap_ms is the wall-clock capture time, or None.
    on_error(message) runs on the main loop (bus errors)."""

    def __init__(self, geom, video, display_name, on_au, on_error, fps=None):
        self.on_au = on_au
        self.on_error = on_error
        self.normaliser = annexb.Normaliser()
        self.frames = 0
        self._captured = 0               # buffers into the leaky queue
        self._encoded = 0                # buffers out of it, towards the encoder
        self._gated = 0                  # dropped on purpose: nothing changed on screen
        self._gate_until = 0.0           # monotonic time until which frames are converted
        self._always_open = (video.change_tail_ms == 0)
        self._last_health = (time.monotonic(), 0)
        desc = describe(geom, video, display_name, fps)
        log.debug("pipeline: %s", desc)
        self.pipe = Gst.parse_launch(desc)
        self.sink = self.pipe.get_by_name("out")
        self.sink.connect("new-sample", self._on_sample)
        self._count(self.pipe.get_by_name("q").get_static_pad("sink"), "_captured")
        self.pipe.get_by_name("conv").get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, self._gate)
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)

    def start(self):
        if self.pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("pipeline failed to start")

    def pause(self):
        """Idle: stop capturing and encoding. A live source produces nothing while paused;
        the receiver keeps showing the last frame and pings keep the link alive."""
        self.pipe.set_state(Gst.State.PAUSED)

    def resume(self):
        self.pipe.set_state(Gst.State.PLAYING)

    def stop(self):
        self.pipe.set_state(Gst.State.NULL)
        self.pipe.get_bus().remove_signal_watch()

    def open_gate(self, seconds: float):
        """Something on the iPad's monitor changed: convert and encode frames for `seconds`.
        Capture is cheap (~1.5 % of a core at 60 fps); colour conversion and encoding are
        not, so frames captured while nothing changes are dropped before them — raw frames,
        never encoded ones (§4). Thread-safe: a float store."""
        self._gate_until = max(self._gate_until, time.monotonic() + seconds)

    def _gate(self, pad, info):
        if self._always_open or time.monotonic() < self._gate_until:
            self._encoded += 1
            return Gst.PadProbeReturn.OK
        self._gated += 1
        return Gst.PadProbeReturn.DROP

    def _count(self, pad, attr):
        def probe(pad, info):
            setattr(self, attr, getattr(self, attr) + 1)
            return Gst.PadProbeReturn.OK
        pad.add_probe(Gst.PadProbeType.BUFFER, probe)

    def health(self) -> dict:
        """Sender-side counters for the receiver's overlay (upstream §6.2 `ping`)."""
        now = time.monotonic()
        t0, f0 = self._last_health
        self._last_health = (now, self.frames)
        fps = (self.frames - f0) / (now - t0) if now > t0 else 0.0
        # The leaky queue is the only place frames are dropped; one may be in flight.
        dropped = self._captured - self._encoded - self._gated - 1     # the leaky queue's drops
        return {"capFps": round(fps, 1), "encDrops": max(0, dropped),
                "netDrops": 0}

    def _capture_ms(self, buf):
        """Wall-clock ms when ximagesrc grabbed this frame (its PTS is capture running time)."""
        clock = self.pipe.get_clock()
        if clock is None or buf.pts == Gst.CLOCK_TIME_NONE:
            return None
        running_now = clock.get_time() - self.pipe.get_base_time()
        return round(time.time() * 1000 - (running_now - buf.pts) / 1e6)

    def force_idr(self):
        """Next encoded frame is an IDR (answers `kf`)."""
        self.sink.send_event(
            GstVideo.video_event_new_upstream_force_key_unit(Gst.CLOCK_TIME_NONE, True, 0))

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            au, is_idr = self.normaliser.access_unit(bytes(info.data))
        finally:
            buf.unmap(info)
        if not au:
            return Gst.FlowReturn.OK
        try:
            self.on_au(au, is_idr, self._capture_ms(buf))
        except OSError:               # socket gone or wedged; the session has already ended
            return Gst.FlowReturn.EOS
        self.frames += 1
        return Gst.FlowReturn.OK

    def _on_bus_error(self, bus, msg):
        err, dbg = msg.parse_error()
        log.warning("pipeline error: %s (%s)", err.message, dbg)  # the session decides if it is fatal
        self.on_error(err.message)

"""Capture → encode pipeline (PROJECT_SPEC.md §8.3).

Backpressure: the appsink callback sends synchronously, so a slow socket
blocks the streaming thread and the leaky queue upstream drops *raw* frames.
Encoded frames are never dropped. `pipeline.py` never edits bytes; every
access unit goes through `annexb.Normaliser`.
"""
import logging

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstVideo  # noqa: E402

from . import annexb  # noqa: E402

log = logging.getLogger(__name__)
Gst.init(None)


def describe(geom, video, display_name: str) -> str:
    """gst-launch description for capturing `geom` of the X screen. `video` is [video] config."""
    x, y, w, h = geom
    return (
        f"ximagesrc display-name={display_name} startx={x} starty={y} "
        f"endx={x + w - 1} endy={y + h - 1} use-damage=false "
        f"show-pointer={'true' if video.show_pointer else 'false'} "
        f"! video/x-raw,framerate={video.fps}/1 "
        "! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
        f"! videoconvert n-threads={video.convert_threads} ! video/x-raw,format=NV12 "
        # vaapih264enc, not vah264lpenc: the latter answers force-key-unit with a non-IDR I-frame.
        f"! vaapih264enc tune=low-power rate-control=cqp init-qp={video.qp} max-bframes=0 "
        f"keyframe-period={video.keyframe_period} "
        "! h264parse config-interval=-1 "
        "! video/x-h264,stream-format=byte-stream,alignment=au "
        "! appsink name=out sync=false emit-signals=true max-buffers=0 drop=false"
    )


class Pipeline:
    """on_au(bytes, is_idr) runs on the GStreamer streaming thread and may block; raising
    OSError stops the stream. on_error(message) runs on the main loop (bus errors)."""

    def __init__(self, geom, video, display_name, on_au, on_error):
        self.on_au = on_au
        self.on_error = on_error
        self.normaliser = annexb.Normaliser()
        self.frames = 0
        desc = describe(geom, video, display_name)
        log.debug("pipeline: %s", desc)
        self.pipe = Gst.parse_launch(desc)
        self.sink = self.pipe.get_by_name("out")
        self.sink.connect("new-sample", self._on_sample)
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)

    def start(self):
        if self.pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("pipeline failed to start")

    def stop(self):
        self.pipe.set_state(Gst.State.NULL)
        self.pipe.get_bus().remove_signal_watch()

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
            self.on_au(au, is_idr)
        except OSError:               # socket gone or wedged; the session has already ended
            return Gst.FlowReturn.EOS
        self.frames += 1
        return Gst.FlowReturn.OK

    def _on_bus_error(self, bus, msg):
        err, dbg = msg.parse_error()
        log.warning("pipeline error: %s (%s)", err.message, dbg)  # the session decides if it is fatal
        self.on_error(err.message)

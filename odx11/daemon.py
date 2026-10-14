# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sessions, the device state machine, and the event loop; owns every timer
(PROJECT_SPEC.md §7.3–7.4, §8.4).

Three layers, the first two pure so unit tests drive them with fakes:
- `Session`: one receiver connection's message handling.
- `Daemon`: the §8.4 state machine across connections — attach/detach, dial,
  redial, grace, teardown. Timers come from an injected `call_later`.
- `run`: GLib wiring — usbmuxd Listen, the per-connection socket and ping
  timer, RandR events, signals. Nothing polls (§4).
"""
import logging
import os
import signal
import socket
import subprocess
import threading
import time

from dataclasses import fields

from . import __version__, config, notify, protocol, state, upgrade, usbmux
from .activity import ActivityMonitor

log = logging.getLogger(__name__)

# How a session ended decides what the daemon does next (§8.4).
GRACE = "grace"          # EOF, silence, sleeping, errors: keep the display, redial
TEARDOWN = "teardown"    # closing (app quit), bad hello: remove the display, keep dialing
PAUSE = "pause"          # user disabled the monitor: remove it, stop until replug


def now_ms() -> int:
    return int(time.time() * 1000)


class Session:
    """One connection's message handling.

    display: attach(w, h, device_cfg) -> geometry, geometry() -> geometry | None
    injector: set_geometry(g), handle(msg), release_all()
    make_pipeline(geometry, on_au, on_error) -> object with start(), stop(), force_idr()
    send(bytes): thread-safe, may raise OSError.
    call_later(seconds, fn) -> cancel(): for the rotation debounce.
    on_end(): the session has ended (`ended`/`end_kind` say why). May run on any thread.
    on_hello(): a valid hello arrived (the daemon ends its grace period)."""

    def __init__(self, cfg, send, display, injector, make_pipeline, clock=now_ms,
                 on_end=None, on_hello=None, call_later=None, monotonic=time.monotonic, on_change=None):
        self.cfg = cfg
        self.on_change = on_change       # something the state file shows changed (may be any thread)
        self.device_name = None
        self.on_end = on_end
        self.on_hello = on_hello
        self.call_later = call_later
        self.send = send
        self.display = display
        self.injector = injector
        self.make_pipeline = make_pipeline
        self.clock = clock
        self.pipeline = None
        self.geom = None                 # geometry the running pipeline captures
        self._gen = 0                    # pipeline generation; errors from older ones are stale
        self.panel = None                # panel the display is built for
        self._pending_panel = None
        self._cancel_debounce = None
        self.device_id = None
        self.ended = None                # reason string once the session is over
        self.end_kind = None
        self._unknown = set()
        self.monotonic = monotonic
        self.paused = False              # idle gate: pipeline paused while nothing changes
        self._last_activity = 0.0
        self._cancel_idle = None

    # --- inbound ---------------------------------------------------------------
    def on_payload(self, payload: bytes):
        msg = protocol.decode_control(payload)
        if msg is None:
            log.debug("ignoring unparseable control payload (%d bytes)", len(payload))
            return
        handler = getattr(self, "_on_" + msg["type"], None)
        if handler is None:
            if msg["type"] not in self._unknown:
                self._unknown.add(msg["type"])
                log.info("ignoring unknown message type %r (logged once)", msg["type"])
            return
        handler(msg)

    def _on_hello(self, msg):
        self.send(protocol.welcome())            # reply to every hello, repeats included
        panel = protocol.panel_pixels(msg)
        if panel is None:
            log.error("hello without a usable panel size: %s", msg)
            self.end("bad hello", TEARDOWN)
            return
        if self.panel is None:
            self.device_id = msg.get("id")
            self.device_name = msg.get("device") if isinstance(msg.get("device"), str) else None
            log.info("hello from %s %s: panel %dx%d, pv %s", msg.get("device", "?"),
                     self.device_id, panel[0], panel[1], msg.get("pv", 1))
            if self.on_hello:
                self.on_hello()
            self._apply_panel(panel)
            return
        if panel == (self._pending_panel or self.panel):
            return
        # Rotation arrives as a flurry of hellos: settle on the last one (§7.3).
        self._pending_panel = panel
        if self._cancel_debounce:
            self._cancel_debounce()
        delay = self.cfg.session.rotation_debounce_ms / 1000
        if self.call_later and delay > 0:
            self._cancel_debounce = self.call_later(delay, self._apply_pending)
        else:
            self._apply_pending()

    def _apply_pending(self):
        self._cancel_debounce = None
        panel, self._pending_panel = self._pending_panel, None
        if panel and panel != self.panel and not self.ended:
            log.info("panel now %dx%d — rebuilding display and stream", *panel)
            self._apply_panel(panel)

    def _apply_panel(self, panel):
        self.panel = panel
        self._stop_pipeline()
        self._start_pipeline(self.display.attach(panel[0], panel[1], self.cfg.device(self.device_id)))

    def _start_pipeline(self, geom):
        self.geom = geom
        self.injector.set_geometry(geom)
        self._gen += 1
        gen = self._gen
        self.pipeline = self.make_pipeline(geom, self._send_au, lambda m: self._pipeline_error(m, gen))
        self.pipeline.start()                    # a fresh encoder opens with an IDR
        self.paused = False
        self.on_activity()
        self._changed()

    def _changed(self):
        if self.on_change:
            self.on_change()

    def rebuild_display(self, new_port: bool):
        """Text size or port changed: rebuild the monitor in place and restart the stream —
        the connection to the iPad stays up. A new port means unforcing the old one first."""
        if self.panel is None or self.ended:
            return
        log.info("display settings changed — rebuilding the virtual monitor")
        self._stop_pipeline()
        if new_port:
            self.display.teardown()
        self._apply_panel(self.panel)

    def restart_pipeline(self):
        """New video settings: same monitor, fresh encoder (starts with an IDR)."""
        if self.pipeline is not None and self.geom is not None and not self.ended:
            log.info("video settings changed — restarting the stream")
            self._stop_pipeline()
            self._start_pipeline(self.geom)

    # --- idle gate (AC 23) -------------------------------------------------------
    def on_activity(self):
        """Something may have changed on screen: make sure frames flow, and push the
        pause back. Cheap enough for every mouse event: one timer per idle period,
        re-checked when it fires, instead of a reschedule per event."""
        if self.pipeline is None or self.ended:
            return
        self._last_activity = self.monotonic()
        if self.paused:
            self.paused = False
            self.pipeline.resume()
            log.debug("activity: capture resumed")
            self._changed()
        if self._cancel_idle is None and self.call_later and self.cfg.video.idle_pause_s > 0:
            self._cancel_idle = self.call_later(self.cfg.video.idle_pause_s, self._idle_check)

    def _idle_check(self):
        self._cancel_idle = None
        if self.pipeline is None or self.ended or self.paused:
            return
        quiet = self.monotonic() - self._last_activity
        if quiet < self.cfg.video.idle_pause_s:
            self._cancel_idle = self.call_later(self.cfg.video.idle_pause_s - quiet, self._idle_check)
            return
        self.paused = True
        self.pipeline.pause()                    # the receiver keeps showing the last frame
        log.debug("idle %.1f s: capture paused", quiet)
        self._changed()

    def on_display_change(self):
        """RandR says something changed (main thread). Follow the virtual output if it moved,
        was resized or rotated — e.g. by the user in Cinnamon's Display settings."""
        if self.geom is None or self.ended:
            return
        geom = self.display.geometry()
        if geom is None:
            self.end("virtual monitor was disabled", PAUSE)
        elif geom != self.geom:
            log.info("virtual monitor now %dx%d+%d+%d — restarting capture", geom.w, geom.h, geom.x, geom.y)
            self._stop_pipeline()
            self._start_pipeline(geom)

    def _on_ping(self, msg):
        self.send(protocol.pong(msg.get("t"), self.clock()))

    def _on_touch(self, msg):
        self.on_activity()
        self.injector.handle(msg)

    _on_scroll = _on_touch

    def _on_kf(self, msg):
        log.debug("kf: forcing IDR")
        if self.pipeline:
            self.on_activity()                   # paused: capture a fresh frame to carry the IDR
            self.pipeline.force_idr()

    def _on_stats(self, msg):
        log.info("PHONE-STATS %s", {k: v for k, v in msg.items() if k != "type"})

    def _on_sleeping(self, msg):
        self.end("receiver sleeping", GRACE)

    def _on_closing(self, msg):
        self.end("receiver closing", TEARDOWN)

    # --- outbound --------------------------------------------------------------
    def _send_au(self, au: bytes, is_idr: bool, cap_ms=None):
        # Runs on the streaming thread: no X calls here. A failed send ends the session;
        # re-raising tells the pipeline to stop.
        try:
            self.send(protocol.encode_video(au, cap_ms, self.clock() if cap_ms is not None else None))
        except OSError as e:
            self.end(f"send failed: {e}", GRACE)
            raise

    def _pipeline_error(self, message, gen):
        """Bus error (main thread). Capture fails when the output moves before we hear the
        RandR event, so a geometry change means rebuild, not end."""
        if gen != self._gen or self.ended:
            return                               # from a pipeline already replaced
        if self.display.geometry() != self.geom:
            self.on_display_change()
        else:
            self.end(f"pipeline error: {message}", GRACE)

    def ping(self):
        health = getattr(self.pipeline, "health", None)
        self.send(protocol.ping(**(health() if health else {})))

    # --- lifecycle -------------------------------------------------------------
    def end(self, reason, kind=GRACE):
        if self.ended is None:
            self.ended, self.end_kind = reason, kind
            log.info("session ending: %s", reason)
            if self.on_end:
                self.on_end()
            self._changed()

    def _stop_pipeline(self):
        if self.pipeline:
            self.pipeline.stop()
            self.pipeline = None

    def stop(self):
        if self._cancel_debounce:
            self._cancel_debounce()
            self._cancel_debounce = None
        if self._cancel_idle:
            self._cancel_idle()
            self._cancel_idle = None
        self._stop_pipeline()
        self.injector.release_all()


class Daemon:
    """The §8.4 state machine. One iPad at a time: the first USB device attached.

    dial(device_id) -> socket; raises usbmux.MuxError (RESULT_REFUSED: app not open) or OSError.
    open_connection(sock, on_end(kind, reason), on_hello()) -> object with close() and .session.
    display: teardown(), forced.  call_later(seconds, fn) -> cancel()."""

    IDLE, DIALING, CONNECTED, GRACE_WAIT, PAUSED = "idle", "dialing", "connected", "grace", "paused"

    def __init__(self, cfg, dial, display, open_connection, call_later):
        self.cfg = cfg
        self.dial = dial
        self.display = display
        self.open_connection = open_connection
        self.call_later = call_later
        self.devices = {}                # device_id -> serial, every USB device usbmuxd reports
        self.active = None
        self.on_change = None            # the published state may have changed
        self.refused = 0                 # dials refused since this iPad was attached
        self.grace_until = None          # wall-clock end of the grace period
        self._state = self.IDLE
        self.conn = None
        self._cancel_redial = None
        self._cancel_grace = None

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        if value != self._state:
            self._state = value
            if value != self.GRACE_WAIT:
                self.grace_until = None
            self._changed()

    def _changed(self):
        if self.on_change:
            self.on_change()

    # --- usbmuxd events --------------------------------------------------------
    def on_attached(self, device_id, serial=None):
        self.devices[device_id] = serial
        if self.active is None:
            log.info("iPad attached: %s", serial or device_id)
            self.active = device_id
            self.refused = 0
            self.state = self.DIALING
            self._dial()

    def on_detached(self, device_id):
        self.devices.pop(device_id, None)
        if device_id != self.active:
            return
        log.info("iPad detached: tearing down")   # cable pull is intent: no grace (§8.4)
        self._reset(self.IDLE)
        self.active = None
        if self.devices:                           # another one is still plugged in
            self.on_attached(*next(iter(self.devices.items())))

    def on_mux_gone(self):
        """usbmuxd exited (it does when the last device leaves): everything detached."""
        for device_id in list(self.devices):
            self.on_detached(device_id)

    # --- dialing ---------------------------------------------------------------
    def _schedule_redial(self):
        self._cancel(redial=True)
        delay = self.cfg.session.redial_grace_s if self.state == self.GRACE_WAIT else self.cfg.session.redial_s
        self._cancel_redial = self.call_later(delay, self._redial)

    def _redial(self):
        self._cancel_redial = None
        if self.state in (self.DIALING, self.GRACE_WAIT) and self.conn is None:
            self._dial()

    def _dial(self):
        try:
            sock = self.dial(self.active)
        except usbmux.MuxError as e:
            if e.result == usbmux.RESULT_BADDEV:
                log.info("usbmuxd: bad device — treating as detached")
                self.on_detached(self.active)
                return
            log.debug("dial refused (app not open?)")
            self.refused += 1
            self._changed()
            self._schedule_redial()
            return
        except OSError as e:
            log.debug("dial failed: %s", e)
            self._schedule_redial()
            return
        log.info("connected to the receiver")
        self.conn = self.open_connection(sock, self._connection_ended, self._hello)
        if self.state != self.GRACE_WAIT:          # in grace, the grace timer runs until hello
            self.state = self.CONNECTED

    def _hello(self):
        self.refused = 0
        if self.state == self.GRACE_WAIT:
            log.info("receiver back within grace — resuming")
            self._cancel(grace=True)
        self.state = self.CONNECTED

    def _connection_ended(self, kind, reason):
        self.conn = None
        if kind == PAUSE:
            log.info("pausing until the iPad is replugged")
            self._reset(self.PAUSED)
            return
        if kind == TEARDOWN or not self.display.forced:
            self._teardown()
            self.state = self.DIALING
        elif self.state != self.GRACE_WAIT:
            self.grace_until = time.time() + self.cfg.session.grace_s
            self.state = self.GRACE_WAIT
            log.info("keeping the display for %g s while the receiver comes back", self.cfg.session.grace_s)
            self._cancel_grace = self.call_later(self.cfg.session.grace_s, self._grace_expired)
        self._schedule_redial()

    def _grace_expired(self):
        self._cancel_grace = None
        log.info("grace period over — removing the display")
        if self.conn is not None:                  # dialed but no hello yet: give up on it
            conn, self.conn = self.conn, None
            conn.close()
        self._teardown()
        self.state = self.DIALING
        self._schedule_redial()

    # --- lifecycle -------------------------------------------------------------
    def _cancel(self, redial=False, grace=False):
        if redial and self._cancel_redial:
            self._cancel_redial()
            self._cancel_redial = None
        if grace and self._cancel_grace:
            self._cancel_grace()
            self._cancel_grace = None

    def _teardown(self):
        self.display.teardown()

    def _reset(self, state):
        self._cancel(redial=True, grace=True)
        if self.conn is not None:
            conn, self.conn = self.conn, None
            conn.close()
        self._teardown()
        self.state = state

    def rebuild_display(self, new_port: bool):
        """Display settings changed (text size, port). Streaming: rebuild in place, staying
        connected. Otherwise (grace, waiting): drop any kept monitor so the next hello builds
        it fresh with the new settings."""
        session = self.conn.session if self.conn is not None else None
        if session is not None and session.panel is not None and not session.ended:
            session.rebuild_display(new_port)
        elif self.display.forced:
            log.info("display settings changed — the monitor is rebuilt on the next connection")
            self._cancel(grace=True)
            self._teardown()
            if self.state == self.GRACE_WAIT:
                self.state = self.DIALING

    def shutdown(self):
        self._reset(self.IDLE)


# --- GLib wiring ---------------------------------------------------------------

def glib_call_later(seconds, fn):
    from gi.repository import GLib
    alive = [True]

    def fire():
        if alive[0]:
            alive[0] = False
            fn()
        return GLib.SOURCE_REMOVE
    source = GLib.timeout_add(max(0, int(seconds * 1000)), fire)

    def cancel():
        if alive[0]:
            alive[0] = False
            GLib.source_remove(source)
    return cancel


class Connection:
    """One receiver socket on the GLib loop: reader, ping/silence tick, a Session."""

    def __init__(self, cfg, sock, display, injector, make_pipeline, on_end, on_hello, on_change=None):
        from gi.repository import GLib
        self._GLib = GLib
        self.cfg = cfg
        self.sock = sock
        self._on_end = on_end
        self._closed = False
        self._lock = threading.Lock()
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # A receiver that stops reading must not wedge sendall (and the lock) forever.
        sock.settimeout(cfg.session.silence_timeout_s)
        self.session = Session(cfg, self._send, display, injector, make_pipeline,
                               on_end=lambda: GLib.idle_add(self._finish_from_session),
                               on_hello=on_hello, call_later=glib_call_later,
                               on_change=(lambda: GLib.idle_add(lambda: on_change() and False))
                               if on_change else None)
        self.deframer = protocol.Deframer()
        self.last_rx = time.monotonic()
        self.display = display
        self._watches = [
            GLib.io_add_watch(sock.fileno(), GLib.PRIORITY_DEFAULT,
                              GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._on_readable),
            GLib.timeout_add(int(cfg.session.ping_interval_s * 1000), self._on_tick),
        ]
        self._safe(self.session.ping)

    def _send(self, data):
        with self._lock:
            self.sock.sendall(data)

    def _safe(self, fn):
        try:
            fn()
        except OSError as e:
            self.session.end(f"send failed: {e}", GRACE)

    def _on_readable(self, fd, cond):
        GLib = self._GLib
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            self.session.end("connection closed", GRACE)
            return GLib.SOURCE_REMOVE
        try:
            data = self.sock.recv(65536)
        except OSError as e:
            self.session.end(f"recv failed: {e}", GRACE)
            return GLib.SOURCE_REMOVE
        if not data:
            self.session.end("receiver closed the connection", GRACE)
            return GLib.SOURCE_REMOVE
        self.last_rx = time.monotonic()
        try:
            for payload in self.deframer.feed(data):
                self.session.on_payload(payload)
        except protocol.ProtocolError as e:
            self.session.end(f"protocol error: {e}", GRACE)
        except OSError as e:
            self.session.end(f"send failed: {e}", GRACE)
        # Xlib queues events it reads while waiting for replies (attach's queries), so the
        # socket can look idle with a RandR event pending: check the queue here too.
        if self.display.drain_events():
            self.session.on_display_change()
        return GLib.SOURCE_REMOVE if self.session.ended else GLib.SOURCE_CONTINUE

    def _on_tick(self):
        # The one periodic timer: liveness both ways (§7.3). Silence is detected between
        # silence_timeout_s and silence_timeout_s + ping_interval_s.
        if time.monotonic() - self.last_rx > self.cfg.session.silence_timeout_s:
            self.session.end(f"no bytes for {self.cfg.session.silence_timeout_s:g} s", GRACE)
            return self._GLib.SOURCE_REMOVE
        self._safe(self.session.ping)
        return self._GLib.SOURCE_REMOVE if self.session.ended else self._GLib.SOURCE_CONTINUE

    def _finish_from_session(self):
        if not self._closed:
            self.close()
            self._on_end(self.session.end_kind, self.session.ended)
        return self._GLib.SOURCE_REMOVE

    def close(self):
        """Stop everything for this connection. Does not report back to the daemon."""
        if self._closed:
            return
        self._closed = True
        ctx = self._GLib.MainContext.default()
        for w in self._watches:
            if ctx.find_source_by_id(w):         # sources that returned SOURCE_REMOVE are gone
                self._GLib.source_remove(w)
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # unblocks a streaming thread stuck in sendall
        except OSError:
            pass
        self.session.stop()
        self.sock.close()


class MuxWatcher:
    """usbmuxd Attached/Detached → Daemon, following usbmuxd as it starts and exits.

    usbmuxd is started by udev when an Apple device appears and exits with the last
    one, removing its socket; a Gio file monitor (inotify) on that path tells us when
    to Listen again. No polling."""

    def __init__(self, socket_path, daemon):
        from gi.repository import Gio, GLib
        self._GLib = GLib
        self.path = socket_path
        self.daemon = daemon
        self.sock = None
        self._watch = None
        self.reader = None
        self.monitor = Gio.File.new_for_path(socket_path).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", self._on_file_event)
        self._listen()

    def _on_file_event(self, monitor, f, other, event):
        from gi.repository import Gio
        if event == Gio.FileMonitorEvent.CREATED and self.sock is None:
            log.debug("usbmuxd socket appeared")
            self._listen()

    def _listen(self):
        if not os.path.exists(self.path):
            log.info("waiting for an iPad (usbmuxd not running)")
            return
        try:
            self.sock = usbmux.listen(self.path)
        except (OSError, EOFError, usbmux.MuxError) as e:
            log.debug("usbmuxd Listen failed: %s", e)   # stale socket; wait for the next CREATED
            self.sock = None
            return
        log.debug("listening for devices on %s", self.path)
        self.reader = usbmux.MessageReader()
        self._watch = self._GLib.io_add_watch(self.sock.fileno(), self._GLib.PRIORITY_DEFAULT,
                                              self._GLib.IO_IN | self._GLib.IO_HUP | self._GLib.IO_ERR,
                                              self._on_readable)

    def _on_readable(self, fd, cond):
        data = b""
        if not cond & (self._GLib.IO_HUP | self._GLib.IO_ERR):
            try:
                data = self.sock.recv(65536)
            except OSError:
                data = b""
        if not data:
            log.debug("usbmuxd went away")
            self.sock.close()
            self.sock = None
            self.daemon.on_mux_gone()
            if os.path.exists(self.path):          # restarted already: listen again
                self._listen()
            return self._GLib.SOURCE_REMOVE
        for msg in self.reader.feed(data):
            kind = msg.get("MessageType")
            if kind == "Attached" and usbmux.is_usb(msg):
                self.daemon.on_attached(msg["DeviceID"], msg["Properties"].get("SerialNumber"))
            elif kind == "Detached":
                self.daemon.on_detached(msg["DeviceID"])
        return self._GLib.SOURCE_CONTINUE

    def close(self):
        if self._watch and self._GLib.MainContext.default().find_source_by_id(self._watch):
            self._GLib.source_remove(self._watch)
        if self.sock:
            self.sock.close()


def facts(daemon, display) -> dict:
    """What the state file publishes (§8.8): facts only — `state.describe` words them."""
    session = daemon.conn.session if daemon.conn is not None else None
    streaming = session is not None and session.pipeline is not None and not session.ended
    st = {Daemon.IDLE: state.IDLE, Daemon.DIALING: state.DIALING, Daemon.GRACE_WAIT: state.GRACE,
          Daemon.PAUSED: state.PAUSED}.get(daemon.state)
    if daemon.state == Daemon.CONNECTED:
        st = state.STREAMING if streaming else state.DIALING
    out = {"state": st, "pid": os.getpid(), "version": upgrade.read_version() or __version__,
           "refused": daemon.refused,
           "connector": getattr(display, "connector", None)}
    if daemon.state == Daemon.GRACE_WAIT and daemon.grace_until:
        out["grace_until"] = round(daemon.grace_until, 1)
    if streaming:
        out["device"] = session.device_name
        out["idle"] = bool(session.paused)
        if session.panel:
            out["panel"] = list(session.panel)
        if session.geom:
            g = session.geom
            out["monitor"] = [g.w, g.h, g.x, g.y]
    return out


class Publisher:
    """Writes the state file and sends the notifications a change earns — main thread only."""

    def __init__(self, cfg, state_file, notifier):
        self.cfg = cfg
        self.file = state_file
        self.notifier = notifier
        self.last = {"state": state.IDLE}

    def publish(self, current: dict):
        try:
            self.file.write(current)
        except OSError as e:
            log.warning("could not write the state file: %s", e)
        if self.notifier is not None:
            for kind, summary, body in notify.decide(self.last, current, self.cfg.notifications):
                self.notifier.send(kind, summary, body)
        self.last = current


def apply_config(cfg, new, daemon, injector) -> str:
    """Copy `new` into the live `cfg` in place (everything holds references into it) and apply
    each change at the lightest level it needs. Returns what was done, for the log."""
    changed = {f.name for f in fields(cfg) if getattr(cfg, f.name) != getattr(new, f.name)}
    new_port = cfg.display.connector != new.display.connector
    for name in changed:
        old_section, new_section = getattr(cfg, name), getattr(new, name)
        if isinstance(old_section, dict):
            old_section.clear()
            old_section.update(new_section)
        else:
            old_section.__dict__.update(new_section.__dict__)
    if not changed:
        return "nothing changed"
    if "input" in changed:
        injector.reconfigure(cfg.input)
    session = daemon.conn.session if daemon.conn is not None else None
    if changed & {"display", "devices"}:
        daemon.rebuild_display(new_port)
        how = "virtual monitor rebuilt"
    elif "video" in changed and session is not None:
        session.restart_pipeline()
        how = "stream restarted"
    else:
        how = "applied live"
    return f"{', '.join(sorted(changed))}: {how}"


def run(cfg, display, injector, make_pipeline, tcp=None, udid=None, config_path=None) -> bool:
    """The daemon's main loop: until SIGINT/SIGTERM, or a package upgrade (returns True: the
    caller exits with upgrade.EX_RESTART so systemd starts the new version). `tcp` = (host,
    port) dials a receiver directly as an always-attached device (tools/fake_receiver.py)."""
    from gi.repository import Gio, GLib

    loop = GLib.MainLoop()
    state_file = state.StateFile()
    publisher = Publisher(cfg, state_file, notify.Notifier())
    pending_publish = [False]

    def schedule_publish():
        # Coalesce a burst of changes (attach → hello → stream) into one write.
        if not pending_publish[0]:
            pending_publish[0] = True

            def go():
                pending_publish[0] = False
                publisher.publish(facts(daemon, display))
                return GLib.SOURCE_REMOVE
            GLib.idle_add(go)

    def dial(device_id):
        if tcp:
            try:
                return socket.create_connection(tcp, timeout=cfg.session.silence_timeout_s)
            except ConnectionRefusedError:
                raise usbmux.MuxError(usbmux.RESULT_REFUSED)
        return usbmux.connect(cfg.transport.usbmuxd_socket, device_id, cfg.transport.device_port)

    def open_connection(sock, on_end, on_hello):
        return Connection(cfg, sock, display, injector, make_pipeline, on_end, on_hello,
                          on_change=schedule_publish)

    daemon = Daemon(cfg, dial, display, open_connection, glib_call_later)
    daemon.on_change = schedule_publish
    activity = ActivityMonitor()               # cheap; idle_pause_s is read live, so always on

    # Settings applied live: the window (or a hand edit) writes the config file; we watch it.
    reload_pending = [None]

    def reload_config():
        reload_pending[0] = None
        try:
            new = config.load(config_path)
        except (ValueError, TypeError, OSError) as e:
            log.error("settings file has an error, keeping the previous settings: %s", e)
            return
        log.info("settings changed — %s", apply_config(cfg, new, daemon, injector))
        schedule_publish()

    def on_config_event(monitor, f, other, event):
        if event in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED,
                     Gio.FileMonitorEvent.DELETED, Gio.FileMonitorEvent.RENAMED,
                     Gio.FileMonitorEvent.MOVED_IN):
            if reload_pending[0]:
                reload_pending[0]()
            reload_pending[0] = glib_call_later(0.3, reload_config)   # editors write in bursts

    config_monitor = None
    if config_path:
        config_monitor = Gio.File.new_for_path(config_path).monitor_file(
            Gio.FileMonitorFlags.WATCH_MOVES, None)
        config_monitor.connect("changed", on_config_event)

    def on_activity(fd, cond):
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            return GLib.SOURCE_REMOVE            # the X watch below handles a dead server
        session = daemon.conn.session if daemon.conn is not None else None
        activity.set_region(session.geom if session is not None and session.pipeline else None)
        if activity.drain() and session is not None:
            session.on_activity()
        return GLib.SOURCE_CONTINUE

    def on_x_event(fd, cond):
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            log.error("X server connection lost (logout?) — stopping")
            loop.quit()
            return GLib.SOURCE_REMOVE
        try:
            changed = display.drain_events()
        except Exception as e:                   # Xlib raises on a dead connection
            log.error("X server connection lost: %s — stopping", e)
            loop.quit()
            return GLib.SOURCE_REMOVE
        if changed and daemon.conn is not None:
            daemon.conn.session.on_display_change()
        return GLib.SOURCE_CONTINUE

    def on_signal():
        log.info("stopping")
        loop.quit()
        return GLib.SOURCE_REMOVE

    watches = [
        GLib.io_add_watch(display.fileno(), GLib.PRIORITY_DEFAULT,
                          GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_x_event),
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, on_signal),
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, on_signal),
    ]
    if activity:
        watches.append(GLib.io_add_watch(activity.fileno(), GLib.PRIORITY_DEFAULT,
                                         GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_activity))
    watcher = None
    if tcp:
        GLib.idle_add(lambda: daemon.on_attached("tcp", f"{tcp[0]}:{tcp[1]}") and False)
    else:
        if udid:
            original = daemon.on_attached
            daemon.on_attached = lambda d, s=None: original(d, s) if s == udid else None
        watcher = MuxWatcher(cfg.transport.usbmuxd_socket, daemon)
    restart = [False]

    def on_upgraded():
        # Load a possibly changed unit file before systemd restarts us with it.
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        restart[0] = True
        loop.quit()

    upgrades = upgrade.Watcher(glib_call_later, on_upgraded) if upgrade.installed_copy() else None

    schedule_publish()
    try:
        loop.run()
    finally:
        if watcher:
            watcher.close()
        daemon.shutdown()
        state_file.clear()                       # no file = Off
        if config_monitor:
            config_monitor.cancel()
        if upgrades:
            upgrades.cancel()
        if activity:
            activity.close()
        ctx = GLib.MainContext.default()
        for w in watches:
            if ctx.find_source_by_id(w):
                GLib.source_remove(w)
    return restart[0]


def display_name() -> str:
    return os.environ.get("DISPLAY", ":0")

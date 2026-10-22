# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""The daemon's main loop end to end: the real GLib loop, real sockets and the real state file,
with a fake receiver standing in for the iPad. Only the X server side (display, activity) and
GStreamer are fakes. Stopped by SIGTERM, as systemd stops it."""
import json
import sys
import os
import signal
import socket
import struct
import threading
import time

import pytest

from odx11 import activity, config, daemon, notify, usbmux
from odx11.display import Geometry

S4 = b"\x00\x00\x00\x01"
AU = S4 + b"\x67\x64\x00\x28\xac" + S4 + b"\x68\xee\x3c\x80" + S4 + b"\x65\x88\x84\x00\x10"


class FakeDisplay:
    connector = "HDMI-A-1"

    def __init__(self):
        self.r, self.w = os.pipe()
        self.forced, self.geom, self.attached = False, None, []

    def fileno(self):
        return self.r

    def drain_events(self):
        return False

    def attach(self, panel_w, panel_h, device_cfg, limits=None):
        self.forced, self.geom = True, Geometry(239, 1080, 1568, 1088)
        self.attached.append((panel_w, panel_h))
        return self.geom

    def geometry(self):
        return self.geom

    def teardown(self):
        self.forced = False


class FakeActivity:
    def __init__(self):
        self.r, self.w = os.pipe()
        self.draw_cursor = True
        self.closed = False

    def fileno(self):
        return self.r

    def set_region(self, rect):
        pass

    def drain(self):
        return activity.Activity(False, None, False, False)

    def pointer_position(self):
        return (300, 1200)

    def cursor_image(self):
        return 1, 1, 0, 0, [0xFF000000], 1

    def close(self):
        self.closed = True


class FakeInjector:
    def set_geometry(self, g): pass
    def handle(self, m): pass
    def release_all(self): pass
    def reconfigure(self, cfg): pass


class Receiver(threading.Thread):
    """The iPad's side: listens, sends hello, collects what the sender transmits."""

    def __init__(self, script):
        super().__init__(daemon=True)
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.script, self.control, self.video, self.error = script, [], [], None

    def run(self):
        try:
            conn, _ = self.server.accept()
            conn.settimeout(5)
            self.conn = conn
            self.send({"type": "hello", "id": "test-ipad", "name": "iPad Air", "pv": 3,
                       "panel": {"pixelsWide": 2360, "pixelsHigh": 1640, "scale": 2}})
            self.script(self)
        except Exception as e:                      # surfaced by the test
            self.error = e
        finally:
            os.kill(os.getpid(), signal.SIGTERM)    # stop the daemon like systemd does

    def send(self, msg):
        p = json.dumps(msg).encode()
        self.conn.sendall(struct.pack(">I", len(p)) + p)

    def read_until(self, done, timeout=5):
        end, buf = time.monotonic() + timeout, b""
        while not done(self) and time.monotonic() < end:
            buf += self.conn.recv(65536)
            while len(buf) >= 4 and len(buf) >= 4 + struct.unpack(">I", buf[:4])[0]:
                n = struct.unpack(">I", buf[:4])[0]
                payload, buf = buf[4:4 + n], buf[4 + n:]
                if payload[:1] == b"{" and b"\x00" not in payload:
                    self.control.append(json.loads(payload))
                else:
                    self.video.append(payload)
        assert done(self), "timed out"


def types(r):
    return [m["type"] for m in r.control]


@pytest.fixture
def rig(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(daemon, "ActivityMonitor", FakeActivity)
    sent = []
    monkeypatch.setattr(notify, "Notifier", lambda: type("N", (), {"send": lambda self, *a: sent.append(a)})())
    pipelines = []

    class Pipe:
        def __init__(self, geom, on_au, on_error, fps=None):
            self.on_au, self.geom, self.fps, self.idr = on_au, geom, fps, 0
            pipelines.append(self)
        def start(self): self.on_au(AU, True, None)
        def stop(self): pass
        def pause(self): pass
        def resume(self): pass
        def open_gate(self, s): pass
        def force_idr(self): self.idr += 1

    return {"pipelines": pipelines, "Pipe": Pipe, "tmp": tmp_path, "notified": sent}


def run_daemon(rig, script, cfg_text=""):
    path = rig["tmp"] / "config.toml"
    path.write_text(cfg_text)
    cfg = config.load(str(path))
    r = Receiver(script)
    r.start()
    disp = FakeDisplay()
    def make_pipeline(g, on_au, on_err, fps=None):
        return rig["Pipe"](g, on_au, on_err, fps)
    restart = daemon.run(cfg, disp, FakeInjector(), make_pipeline, tcp=("127.0.0.1", r.port), config_path=str(path))
    r.join(5)
    if r.error:
        raise r.error
    return r, disp, restart


def test_hello_gets_welcome_a_monitor_and_video(rig):
    seen = {}

    def script(r):
        r.read_until(lambda r: "welcome" in types(r) and r.video)
        r.send({"type": "ping", "t": 1234})
        r.read_until(lambda r: "pong" in types(r))
        p = rig["tmp"] / "run" / "odx11" / "state.json"
        end = time.monotonic() + 3
        while time.monotonic() < end and (not p.exists() or json.loads(p.read_text())["state"] != "streaming"):
            time.sleep(0.05)
        seen["state"] = json.loads(p.read_text())

    r, disp, restart = run_daemon(rig, script)
    welcome = next(m for m in r.control if m["type"] == "welcome")
    assert (welcome["pv"], welcome["min"]) == (2, 1)
    assert next(m for m in r.control if m["type"] == "pong")["t"] == 1234
    assert r.video[0].endswith(AU)                          # the first frame is the IDR with SPS/PPS
    assert disp.attached == [(2360, 1640)]
    assert seen["state"]["state"] == "streaming" and seen["state"]["monitor"] == [1568, 1088, 239, 1080]
    assert restart is False
    assert not disp.forced                                  # torn down on the way out
    assert not (rig["tmp"] / "run" / "odx11" / "state.json").exists()     # no file = Off


def test_kf_forces_an_idr(rig):
    def script(r):
        r.read_until(lambda r: r.video)
        r.send({"type": "kf"})
        end = time.monotonic() + 3
        while time.monotonic() < end and not rig["pipelines"][0].idr:
            time.sleep(0.02)

    run_daemon(rig, script)
    assert rig["pipelines"][0].idr == 1


def test_a_settings_change_is_applied_while_streaming(rig):
    def script(r):
        r.read_until(lambda r: r.video)
        (rig["tmp"] / "config.toml").write_text('[video]\npreset = "custom"\nqp = 30\n')
        end = time.monotonic() + 3
        while time.monotonic() < end and len(rig["pipelines"]) < 2:
            time.sleep(0.05)

    run_daemon(rig, script)
    assert len(rig["pipelines"]) == 2                      # the stream restarted with the new sharpness


def test_a_broken_settings_file_keeps_the_running_settings(rig, caplog):
    def script(r):
        r.read_until(lambda r: r.video)
        (rig["tmp"] / "config.toml").write_text("[video\n")
        time.sleep(0.8)

    caplog.set_level("ERROR")
    run_daemon(rig, script)
    assert "settings file has an error, keeping the previous settings" in caplog.text
    assert len(rig["pipelines"]) == 1


def test_the_receiver_leaving_keeps_the_monitor_for_grace(rig):
    seen = {}

    def script(r):
        r.read_until(lambda r: r.video)
        r.conn.close()
        p = rig["tmp"] / "run" / "odx11" / "state.json"
        end = time.monotonic() + 3
        while time.monotonic() < end and json.loads(p.read_text())["state"] != "grace":
            time.sleep(0.05)
        seen["state"] = json.loads(p.read_text())["state"]

    run_daemon(rig, script)
    assert seen["state"] == "grace"


# --- the X side of the loop: activity and display events -------------------------------

def test_activity_reaches_the_session(rig, monkeypatch):
    seen = []
    script_acts = [activity.Activity(True, (400, 1300), True, True)]

    class ScriptedActivity(FakeActivity):
        def drain(self):
            os.read(self.r, 1)
            return script_acts.pop() if script_acts else activity.Activity(False, None, False, False)

    made = []
    monkeypatch.setattr(daemon, "ActivityMonitor", lambda: made.append(ScriptedActivity()) or made[-1])
    for name in ("on_activity", "on_pointer", "on_cursor_changed"):
        real = getattr(daemon.Session, name)
        monkeypatch.setattr(daemon.Session, name,
                            lambda self, *a, _n=name, _r=real: (seen.append((_n, a)), _r(self, *a))[1])

    def script(r):
        r.read_until(lambda r: r.video)
        os.write(made[0].w, b"x")
        end = time.monotonic() + 3
        while time.monotonic() < end and len(seen) < 3:
            time.sleep(0.02)

    run_daemon(rig, script, '[video]\ninput_tail_ms = 777\n')
    assert ("on_activity", (777,)) in seen                 # a key press opens the gate for input_tail_ms
    assert ("on_pointer", (400, 1300)) in seen
    assert ("on_cursor_changed", ()) in seen
    assert made[0].closed


def test_a_display_change_reaches_the_session(rig, monkeypatch):
    seen = []
    real = daemon.Session.on_display_change
    monkeypatch.setattr(daemon.Session, "on_display_change", lambda self: (seen.append(1), real(self))[1])
    box = {}

    def script(r):
        r.read_until(lambda r: r.video)
        box["disp"].changed = True
        os.write(box["disp"].w, b"x")
        end = time.monotonic() + 3
        while time.monotonic() < end and not seen:
            time.sleep(0.02)

    class ChangingDisplay(FakeDisplay):
        changed = False
        def drain_events(self):
            if self.changed:
                os.read(self.r, 1)
                self.changed = False
                return True
            return False

    monkeypatch.setattr(sys.modules[__name__], "FakeDisplay", ChangingDisplay)
    orig = ChangingDisplay.__init__
    def init(self):
        orig(self)
        box["disp"] = self
    monkeypatch.setattr(ChangingDisplay, "__init__", init)
    run_daemon(rig, script)
    assert seen


def test_losing_the_x_server_stops_the_daemon(rig, monkeypatch, caplog):
    class DyingDisplay(FakeDisplay):
        def drain_events(self):
            raise ConnectionResetError("X connection broken")

    path = rig["tmp"] / "config.toml"
    path.write_text(f'[transport]\nusbmuxd_socket = "{rig["tmp"] / "no-usbmuxd"}"\n')
    disp = DyingDisplay()
    os.write(disp.w, b"x")                                  # readable at once: the loop notices
    caplog.set_level("INFO")
    restart = daemon.run(config.load(str(path)), disp, FakeInjector(), rig["Pipe"], config_path=str(path))
    assert restart is False
    assert "X server connection lost: X connection broken — stopping" in caplog.text
    assert "waiting for an iPad (usbmuxd not running)" in caplog.text


def test_a_refused_dial_keeps_waiting_for_the_app(rig, caplog):
    path = rig["tmp"] / "config.toml"
    path.write_text("[session]\nredial_s = 0.1\n")
    closed = socket.create_server(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()                                         # nothing listens there now
    threading.Timer(0.6, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    caplog.set_level("DEBUG")
    daemon.run(config.load(str(path)), FakeDisplay(), FakeInjector(), rig["Pipe"],
               tcp=("127.0.0.1", port), config_path=str(path))
    assert rig["pipelines"] == []
    assert caplog.text.count("dial refused (app not open?)") >= 2    # it redialled


# --- one connection's error paths -------------------------------------------------------

def iterate(until, timeout=3):
    from gi.repository import GLib
    ctx, end = GLib.MainContext.default(), time.monotonic() + timeout
    while not until() and time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.005)
    assert until(), "timed out"


def connection(cfg_raw=None):
    ours, theirs = socket.socketpair()
    ended = []
    cfg = config.parse(cfg_raw or {})
    c = daemon.Connection(cfg, ours, FakeDisplay(), FakeInjector(), lambda *a, **k: None,
                          on_end=lambda kind, why: ended.append((kind, why)), on_hello=lambda *a: None)
    return c, theirs, ended


@pytest.mark.parametrize("act,reason", [
    (lambda s: s.sendall(struct.pack(">I", 0)), "protocol error: bad inbound frame length 0"),
    (lambda s: s.shutdown(socket.SHUT_WR), "receiver closed the connection"),   # orderly end of stream
    (lambda s: s.close(), "connection closed"),                                 # hang-up
])
def test_a_bad_frame_or_a_hang_up_ends_the_connection(act, reason):
    c, theirs, ended = connection()
    act(theirs)
    iterate(lambda: ended)
    assert ended == [(daemon.GRACE, reason)]
    c.close()
    c.close()                                              # closing twice is harmless


def test_silence_ends_the_connection():
    c, theirs, ended = connection({"session": {"ping_interval_s": 0.05, "silence_timeout_s": 0.15}})
    iterate(lambda: ended)
    assert ended == [(daemon.GRACE, "no bytes for 0.15 s")]
    theirs.close()


def test_pings_go_out_while_the_receiver_is_quiet():
    c, theirs, ended = connection({"session": {"ping_interval_s": 0.05, "silence_timeout_s": 5}})
    theirs.setblocking(False)
    got = bytearray()

    def two_pings():
        try:
            got.extend(theirs.recv(4096))
        except BlockingIOError:
            pass
        return got.count(b'"type":"ping"') >= 2
    iterate(two_pings)
    assert not ended
    c.close()
    theirs.close()


class BrokenSocket:
    """Wraps a socket whose sends fail, as when the receiver vanished mid-write."""
    def __init__(self, sock):
        self.sock = sock
    def sendall(self, data):
        raise BrokenPipeError("Broken pipe")
    def __getattr__(self, name):
        return getattr(self.sock, name)


def test_a_failed_send_ends_the_session():
    c, theirs, ended = connection({"session": {"ping_interval_s": 0.05, "silence_timeout_s": 5}})
    c.sock = BrokenSocket(c.sock)                          # the next ping cannot be sent
    iterate(lambda: ended)
    assert ended == [(daemon.GRACE, "send failed: Broken pipe")]
    theirs.close()


# --- following usbmuxd as it starts, reports devices and exits --------------------------

class ListenMux:
    """A usbmuxd that accepts Listen subscriptions and pushes events to the latest one."""

    def __init__(self, path):
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(4)
        self.clients = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.server.accept()
            except OSError:
                return
            head = c.recv(16, socket.MSG_WAITALL)
            c.recv(usbmux.HEADER.unpack(head)[0] - 16, socket.MSG_WAITALL)
            c.sendall(usbmux.encode({"MessageType": "Result", "Number": 0}))
            self.clients.append(c)

    def push(self, msg):
        self.clients[-1].sendall(usbmux.encode(msg))


class Recorder:
    def __init__(self):
        self.calls = []
    def on_attached(self, device_id, serial=None):
        self.calls.append(("attached", device_id, serial))
    def on_detached(self, device_id):
        self.calls.append(("detached", device_id))
    def on_mux_gone(self):
        self.calls.append(("gone",))


def test_watcher_follows_usbmuxd_starting_reporting_and_exiting(tmp_path, caplog):
    path = tmp_path / "usbmuxd"
    rec = Recorder()
    caplog.set_level("INFO")
    w = daemon.MuxWatcher(str(path), rec)
    assert "waiting for an iPad (usbmuxd not running)" in caplog.text
    mux = ListenMux(path)                                  # usbmuxd starts: its socket appears
    iterate(lambda: mux.clients and w.sock is not None)
    usb = {"MessageType": "Attached", "DeviceID": 7,
           "Properties": {"ConnectionType": "USB", "SerialNumber": "00008103-ABC"}}
    mux.push(usb)
    mux.push({"MessageType": "Attached", "DeviceID": 8, "Properties": {"ConnectionType": "Network"}})
    mux.push({"MessageType": "Detached", "DeviceID": 7})
    iterate(lambda: len(rec.calls) >= 2)
    assert rec.calls == [("attached", 7, "00008103-ABC"), ("detached", 7)]   # Wi-Fi entries ignored
    mux.clients[-1].close()                                # usbmuxd restarts: socket file still there
    iterate(lambda: ("gone",) in rec.calls and len(mux.clients) == 2)
    mux.push(usb)                                          # the new subscription works
    iterate(lambda: rec.calls[-1] == ("attached", 7, "00008103-ABC"))
    w.close()
    mux.server.close()


class SilentMux(ListenMux):
    """A usbmuxd that ignores the first `silent` subscriptions (stuck on a device handshake),
    then answers like ListenMux."""

    def __init__(self, path, silent=1):
        self.silent = silent
        self.ignored = []
        super().__init__(path)

    def _accept(self):
        while True:
            try:
                c, _ = self.server.accept()
            except OSError:
                return
            if self.silent:
                self.silent -= 1
                self.ignored.append(c)                     # never answered
                continue
            head = c.recv(16, socket.MSG_WAITALL)
            c.recv(usbmux.HEADER.unpack(head)[0] - 16, socket.MSG_WAITALL)
            c.sendall(usbmux.encode({"MessageType": "Result", "Number": 0}))
            self.clients.append(c)


def test_a_usbmuxd_that_never_answers_is_retried_not_waited_on(tmp_path, caplog):
    """2026-10-22: usbmuxd took the Listen request and never replied; the watcher blocked the
    main loop for good (no stop, no iPad). Now it gives up after the time limit, keeps the loop
    running and asks again after the same interval."""
    path = tmp_path / "usbmuxd"
    mux = SilentMux(path)
    caplog.set_level("INFO")
    start = time.monotonic()
    w = daemon.MuxWatcher(str(path), Recorder(), timeout_s=0.2)
    assert time.monotonic() - start < 2                    # did not hang
    assert w.sock is None
    assert "usbmuxd did not answer within 0.2 s" in caplog.text
    iterate(lambda: w.sock is not None)                    # the retry subscribed
    assert len(mux.clients) == 1
    w.close()
    mux.server.close()


def test_closing_the_watcher_cancels_a_pending_retry(tmp_path):
    path = tmp_path / "usbmuxd"
    mux = SilentMux(path, silent=5)
    w = daemon.MuxWatcher(str(path), Recorder(), timeout_s=0.2)
    w.close()
    from gi.repository import GLib
    end = time.monotonic() + 0.6
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)
    assert len(mux.ignored) == 1                           # no retry after close
    mux.server.close()


def test_a_stale_usbmuxd_socket_is_waited_out(tmp_path):
    path = tmp_path / "usbmuxd"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))                                      # a socket file nobody listens on
    s.close()
    w = daemon.MuxWatcher(str(path), Recorder())
    assert w.sock is None                                  # Listen failed: wait for the next CREATED
    w.close()

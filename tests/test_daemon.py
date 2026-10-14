# SPDX-FileCopyrightText: 2026 vibhanshu
# SPDX-License-Identifier: GPL-3.0-or-later
"""The §8.4 state machine, driven with a fake clock — no sockets, X or GLib."""
import json
import os
import plistlib

import pytest

from odx11 import config, usbmux
from odx11.daemon import GRACE, PAUSE, TEARDOWN, Daemon, Session
from odx11.display import Geometry


class Clock:
    def __init__(self):
        self.now = 0.0
        self.timers = []                     # [due, fn, alive]

    def call_later(self, seconds, fn):
        t = [self.now + seconds, fn, True]
        self.timers.append(t)

        def cancel():
            t[2] = False
        return cancel

    def advance(self, seconds):
        end = self.now + seconds
        while True:
            due = [t for t in self.timers if t[2] and t[0] <= end]
            if not due:
                break
            t = min(due, key=lambda t: t[0])
            t[2] = False
            self.now = t[0]
            t[1]()
        self.now = end

    def pending(self):
        return sorted(round(t[0] - self.now, 3) for t in self.timers if t[2])


class FakeDisplay:
    def __init__(self):
        self.forced = False
        self.teardowns = 0

    def teardown(self):
        self.forced = False
        self.teardowns += 1


class FakeConn:
    def __init__(self, on_end, on_hello):
        self.on_end, self.on_hello = on_end, on_hello
        self.closed = False
        self.session = None

    def close(self):
        self.closed = True


class Rig:
    def __init__(self, refuse=0):
        self.clock = Clock()
        self.display = FakeDisplay()
        self.conns = []
        self.dials = 0
        self.refuse = refuse                 # refuse this many dials first (app not open)
        self.d = Daemon(config.parse({}), self.dial, self.display, self.open, self.clock.call_later)

    def dial(self, device_id):
        self.dials += 1
        if self.refuse:
            self.refuse -= 1
            raise usbmux.MuxError(usbmux.RESULT_REFUSED)
        return object()

    def open(self, sock, on_end, on_hello):
        c = FakeConn(on_end, on_hello)
        self.conns.append(c)
        return c

    def hello(self):
        """The receiver's hello: the session attaches the display."""
        self.display.forced = True
        self.conns[-1].on_hello()

    def end(self, kind, reason="x"):
        self.conns[-1].on_end(kind, reason)


def test_attach_dials_and_connects():
    r = Rig()
    r.d.on_attached(1, "UDID")
    assert r.dials == 1 and r.d.state == Daemon.CONNECTED


def test_app_not_open_redials_every_3s():
    r = Rig(refuse=2)
    r.d.on_attached(1)
    assert r.d.state == Daemon.DIALING and r.clock.pending() == [3.0]
    r.clock.advance(3)
    assert r.dials == 2
    r.clock.advance(3)
    assert r.dials == 3 and r.d.state == Daemon.CONNECTED


def test_drop_keeps_display_for_grace_and_resumes_on_hello():
    r = Rig()
    r.d.on_attached(1)
    r.hello()
    r.end(GRACE, "receiver closed the connection")
    assert r.d.state == Daemon.GRACE_WAIT and r.display.teardowns == 0
    r.refuse = 2                             # app switched away for ~2 s
    r.clock.advance(1)
    r.clock.advance(1)
    r.clock.advance(1)                       # third dial (at 1 s cadence) connects
    assert len(r.conns) == 2
    r.hello()
    assert r.d.state == Daemon.CONNECTED and r.display.teardowns == 0
    r.clock.advance(30)                      # the grace timer was cancelled
    assert r.display.teardowns == 0


def test_grace_expiry_tears_down_and_keeps_dialing():
    r = Rig()
    r.d.on_attached(1)
    r.hello()
    r.refuse = 100
    r.end(GRACE, "receiver sleeping")
    r.clock.advance(10)
    assert r.display.teardowns == 1 and r.d.state == Daemon.DIALING
    dials = r.dials
    r.clock.advance(9)                       # back to the slow cadence
    assert r.dials == dials + 3


def test_connected_without_hello_when_grace_expires_is_dropped():
    r = Rig()
    r.d.on_attached(1)
    r.hello()
    r.end(GRACE)
    r.clock.advance(1)                       # redial connects, but no hello follows
    assert len(r.conns) == 2 and r.d.state == Daemon.GRACE_WAIT
    r.clock.advance(9)
    assert r.conns[1].closed and r.display.teardowns == 1


def test_closing_tears_down_immediately_and_redials():
    r = Rig()
    r.d.on_attached(1)
    r.hello()
    r.end(TEARDOWN, "receiver closing")
    assert r.display.teardowns == 1 and r.d.state == Daemon.DIALING and r.clock.pending() == [3.0]


def test_detach_is_intent_no_grace():
    r = Rig()
    r.d.on_attached(1)
    r.hello()
    r.d.on_detached(1)
    assert r.conns[0].closed and r.display.teardowns == 1 and r.d.state == Daemon.IDLE
    assert r.clock.pending() == []


def test_detach_during_grace_cancels_everything():
    r = Rig()
    r.d.on_attached(1)
    r.hello()
    r.end(GRACE)
    r.d.on_detached(1)
    assert r.d.state == Daemon.IDLE and r.clock.pending() == []
    assert r.display.teardowns == 1


def test_usbmuxd_exit_detaches_everything():
    r = Rig()
    r.d.on_attached(1)
    r.hello()
    r.d.on_mux_gone()
    assert r.d.state == Daemon.IDLE and r.display.teardowns == 1 and r.d.devices == {}


def test_drop_before_hello_skips_grace():
    r = Rig()
    r.d.on_attached(1)
    r.end(GRACE)                             # no display was ever attached
    assert r.d.state == Daemon.DIALING


def test_user_disabling_the_monitor_pauses_until_replug():
    r = Rig()
    r.d.on_attached(1)
    r.hello()
    r.end(PAUSE, "virtual monitor was disabled")
    assert r.d.state == Daemon.PAUSED and r.display.teardowns == 1 and r.clock.pending() == []
    r.d.on_detached(1)
    r.d.on_attached(1)
    assert r.d.state == Daemon.CONNECTED


def test_second_ipad_waits_then_takes_over():
    r = Rig()
    r.d.on_attached(1, "A")
    r.d.on_attached(2, "B")
    assert r.d.active == 1 and r.dials == 1
    r.d.on_detached(1)
    assert r.d.active == 2 and r.dials == 2


def test_bad_device_is_treated_as_detached():
    r = Rig()

    def bad(_):
        raise usbmux.MuxError(usbmux.RESULT_BADDEV)
    r.d.dial = bad
    r.d.on_attached(1)
    assert r.d.state == Daemon.IDLE and r.d.active is None


def test_shutdown_tears_down():
    r = Rig()
    r.d.on_attached(1)
    r.hello()
    r.d.shutdown()
    assert r.conns[0].closed and r.display.teardowns == 1 and r.clock.pending() == []


# --- Session: rotation debounce ---------------------------------------------------

class SDisplay:
    def __init__(self):
        self.attached = []

    def attach(self, w, h, dc):
        self.attached.append((w, h))
        return Geometry(0, 0, *((1568, 1088) if w >= h else (1088, 1568)))

    def geometry(self):
        return None


class SInjector:
    def set_geometry(self, g): pass
    def handle(self, m): pass
    def release_all(self): pass


class SPipe:
    def __init__(self, *a): pass
    def start(self): pass
    def stop(self): pass


def hello(w, h):
    return json.dumps({"type": "hello", "id": "x", "panel": {"pixelsWide": w, "pixelsHigh": h, "scale": 2}}).encode()


def test_rotation_flurry_is_debounced_into_one_rebuild():
    clock = Clock()
    s = Session(config.parse({}), lambda b: None, SDisplay(), SInjector(), SPipe, call_later=clock.call_later)
    s.on_payload(hello(2360, 1640))                       # first hello: immediate
    for w, h in [(1640, 2360), (2360, 1640), (1640, 2360)]:
        s.on_payload(hello(w, h))
        clock.advance(0.1)
    assert s.display.attached == [(2360, 1640)]           # nothing yet
    clock.advance(0.3)
    assert s.display.attached == [(2360, 1640), (1640, 2360)]


def test_rotation_back_before_debounce_rebuilds_nothing():
    clock = Clock()
    s = Session(config.parse({}), lambda b: None, SDisplay(), SInjector(), SPipe, call_later=clock.call_later)
    s.on_payload(hello(2360, 1640))
    s.on_payload(hello(1640, 2360))
    s.on_payload(hello(2360, 1640))
    clock.advance(1)
    assert s.display.attached == [(2360, 1640)]


def test_every_hello_still_gets_a_welcome_during_debounce():
    clock = Clock()
    sent = []
    s = Session(config.parse({}), sent.append, SDisplay(), SInjector(), SPipe, call_later=clock.call_later)
    for w, h in [(2360, 1640), (1640, 2360), (2360, 1640)]:
        s.on_payload(hello(w, h))
    assert [json.loads(f[4:])["type"] for f in sent] == ["welcome"] * 3


# --- usbmuxd Listen stream ----------------------------------------------------------

def mux_msg(d):
    body = plistlib.dumps(d)
    return usbmux.HEADER.pack(16 + len(body), 1, 8, 0) + body


def test_mux_reader_handles_split_and_packed_messages():
    stream = mux_msg({"MessageType": "Attached", "DeviceID": 7, "Properties": {"ConnectionType": "USB"}}) \
        + mux_msg({"MessageType": "Detached", "DeviceID": 7})
    r = usbmux.MessageReader()
    got = []
    for i in range(0, len(stream), 13):
        got += r.feed(stream[i:i + 13])
    assert [m["MessageType"] for m in got] == ["Attached", "Detached"]
    assert usbmux.is_usb(got[0]) and not usbmux.is_usb({"Properties": {"ConnectionType": "Network"}})


def test_mux_reader_rejects_garbage_length():
    with pytest.raises(ValueError):
        usbmux.MessageReader().feed(usbmux.HEADER.pack(3, 1, 8, 0))


# --- Session: idle gate (AC 23) ----------------------------------------------------

class GatePipe:
    def __init__(self, *a):
        self.log = []
    def start(self): self.log.append("start")
    def stop(self): self.log.append("stop")
    def pause(self): self.log.append("pause")
    def resume(self): self.log.append("resume")
    def force_idr(self): self.log.append("idr")


def gated(idle=1.0):
    clock = Clock()
    pipes = []

    def make(*a):
        pipes.append(GatePipe())
        return pipes[-1]
    s = Session(config.parse({"video": {"idle_pause_s": idle}}), lambda b: None, SDisplay(), SInjector(),
                make, call_later=clock.call_later, monotonic=lambda: clock.now)
    s.on_payload(hello(2360, 1640))
    return s, clock, pipes


def test_static_screen_pauses_after_idle_period():
    s, clock, pipes = gated()
    clock.advance(0.9)
    assert pipes[0].log == ["start"]
    clock.advance(0.2)
    assert pipes[0].log == ["start", "pause"] and s.paused


def test_activity_resumes_and_postpones_the_pause():
    s, clock, pipes = gated()
    clock.advance(1.5)                                 # paused
    s.on_activity()
    assert pipes[0].log[-1] == "resume" and not s.paused
    clock.advance(0.6)
    s.on_activity()                                    # still busy
    clock.advance(0.6)
    assert not s.paused                                # 0.6 s since last activity
    clock.advance(0.5)
    assert s.paused


def test_a_burst_of_activity_keeps_one_timer():
    s, clock, pipes = gated()
    for _ in range(500):                               # a mouse wiggle
        s.on_activity()
    assert len(clock.pending()) == 1


def test_kf_while_paused_resumes_then_forces_idr():
    s, clock, pipes = gated()
    clock.advance(2)
    s.on_payload(json.dumps({"type": "kf"}).encode())
    assert pipes[0].log[-2:] == ["resume", "idr"]


def test_touch_wakes_capture():
    s, clock, pipes = gated()
    clock.advance(2)
    s.on_payload(json.dumps({"type": "touch", "phase": "began", "x": 0.5, "y": 0.5}).encode())
    assert pipes[0].log[-1] == "resume"


def test_idle_pause_zero_never_pauses():
    s, clock, pipes = gated(idle=0)
    clock.advance(60)
    assert pipes[0].log == ["start"] and clock.pending() == []


def test_stop_cancels_idle_timer():
    s, clock, pipes = gated()
    s.stop()
    assert clock.pending() == []


# --- restart onto a new version after a package upgrade -----------------------------

from odx11 import upgrade  # noqa: E402


def test_upgrade_restarts_only_on_a_real_completed_change():
    assert upgrade.decide("0.2.1", "0.2.2", "install ok installed") == "restart"
    assert upgrade.decide("0.2.1", "0.2.1", "install ok installed") == "ignore"     # same version
    assert upgrade.decide("0.2.1", "0.2.2", "install ok unpacked") == "wait"         # dpkg not done
    assert upgrade.decide("0.2.1", "0.2.2", "install ok half-configured") == "wait"
    assert upgrade.decide("0.2.1", None, "deinstall ok config-files") == "ignore"   # being removed
    assert upgrade.decide(None, "0.2.2", "install ok installed") == "ignore"         # dev run


def test_source_checkout_never_self_restarts():
    assert not upgrade.installed_copy() or upgrade.__file__.startswith("/usr/")


def test_window_only_changes_do_not_restart_the_service():
    assert upgrade.service_changed("abc", "abc") is False
    assert upgrade.service_changed("abc", "def") is True
    assert upgrade.service_changed(None, "def") is True      # upgraded from a package without one
    assert upgrade.service_changed("abc", None) is True


def test_fingerprint_ignores_the_window_but_not_the_service(tmp_path):
    import shutil
    root = os.path.join(os.path.dirname(__file__), "..")
    shutil.copytree(os.path.join(root, "odx11"), tmp_path / "odx11")
    (tmp_path / "packaging").mkdir()
    shutil.copy(os.path.join(root, "packaging", "odx11.service"), tmp_path / "packaging")
    before = upgrade.fingerprint(str(tmp_path))
    (tmp_path / "odx11" / "gui.py").write_text("# window change\n", encoding="utf-8")
    assert upgrade.fingerprint(str(tmp_path)) == before
    with open(tmp_path / "odx11" / "daemon.py", "a") as f:
        f.write("# service change\n")
    assert upgrade.fingerprint(str(tmp_path)) != before


def test_service_modules_never_import_window_only_modules():
    """Guards GUI_ONLY: if the daemon imported one of them, a window-only upgrade
    would leave the service running stale code."""
    import ast
    pkg = os.path.join(os.path.dirname(__file__), "..", "odx11")
    gui_only = {m[:-3] for m in upgrade.GUI_ONLY}
    for name in os.listdir(pkg):
        if not name.endswith(".py") or name in upgrade.GUI_ONLY or name == "__main__.py":
            continue
        tree = ast.parse(open(os.path.join(pkg, name), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                mods = {node.module} if node.module else {a.name for a in node.names}
                assert not mods & gui_only, f"{name} imports {mods & gui_only}"


def test_watcher_reacts_to_dpkg_style_replacement(tmp_path, monkeypatch):
    """The real Watcher on a real GLib loop: dpkg writes a new file and renames it over."""
    gi = pytest.importorskip("gi")
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
    from odx11.daemon import glib_call_later
    monkeypatch.setattr(upgrade, "SETTLE_S", 0.05)

    def dpkg_write(name, text):
        tmp = tmp_path / (name + ".dpkg-new")
        tmp.write_text(text)
        os.replace(tmp, tmp_path / name)

    dpkg_write("VERSION", "0.2.4\n")
    dpkg_write("SERVICE", "aaa\n")
    restarts = []
    w = upgrade.Watcher(glib_call_later, lambda: restarts.append(1), str(tmp_path / "VERSION"),
                        status=lambda: ("install ok installed", ""), service_file=str(tmp_path / "SERVICE"))

    def run_loop(seconds):
        loop = GLib.MainLoop()
        GLib.timeout_add(int(seconds * 1000), loop.quit)
        loop.run()

    dpkg_write("VERSION", "0.2.5\n")                 # window-only update: fingerprint unchanged
    dpkg_write("SERVICE", "aaa\n")
    run_loop(0.5)
    assert restarts == [] and w.running == "0.2.5"

    dpkg_write("SERVICE", "bbb\n")                   # service update
    dpkg_write("VERSION", "0.2.6\n")
    run_loop(0.5)
    assert restarts == [1]
    w.cancel()

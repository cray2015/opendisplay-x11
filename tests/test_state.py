"""State file, its wording, notifications, and live settings (PROJECT_SPEC.md §8.8)."""
import json
import os

from odx11 import config, notify, state
from odx11.daemon import Daemon, apply_config, facts
from odx11.display import Geometry


# --- state file ---------------------------------------------------------------------

def test_write_is_atomic_and_skips_unchanged(tmp_path):
    f = state.StateFile(str(tmp_path / "odx11" / "state.json"), clock=lambda: 5.0)
    assert f.write({"state": state.IDLE, "pid": os.getpid()})
    assert not f.write({"state": state.IDLE, "pid": os.getpid()})       # nothing changed
    data = json.loads((tmp_path / "odx11" / "state.json").read_text())
    assert data == {"state": "idle", "pid": os.getpid(), "updated": 5.0}
    assert [p.name for p in (tmp_path / "odx11").iterdir()] == ["state.json"]   # no temp left


def test_clear_means_off(tmp_path):
    p = str(tmp_path / "state.json")
    f = state.StateFile(p)
    f.write({"state": state.STREAMING, "pid": os.getpid()})
    f.clear()
    assert state.read(p) == {"state": state.OFF}


def test_file_left_by_a_dead_daemon_reads_as_off(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"state": "streaming", "pid": 2 ** 22 + 12345}))
    assert state.read(str(p)) == {"state": state.OFF}


def test_a_problem_outlives_its_process(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"state": "problem", "problem": "no free video port", "pid": 2 ** 22 + 1}))
    assert state.read(str(p))["state"] == state.PROBLEM


def test_garbage_reads_as_off(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    assert state.read(str(p)) == {"state": state.OFF}


# --- wording --------------------------------------------------------------------------

def test_every_state_has_plain_words():
    for st in (state.OFF, state.PROBLEM, state.IDLE, state.DIALING, state.STREAMING, state.GRACE, state.PAUSED):
        head, action, level = state.describe({"state": st})
        assert head and head != "Unknown state" and level


def test_streaming_names_device_size_and_idle():
    head, action, level = state.describe({"state": "streaming", "device": "iPad",
                                          "monitor": [1568, 1088, 239, 1080], "idle": True})
    assert head == "Showing your desktop on iPad (1568 × 1088)"
    assert action == "paused while nothing changes" and level == state.OK


def test_grace_counts_down():
    head, _, _ = state.describe({"state": "grace", "grace_until": 108.0}, now=100.0)
    assert head == "Connection lost — keeping the screen for 8 s"


def test_problem_shows_the_reason():
    _, action, level = state.describe({"state": "problem", "problem": "This computer has no free video port."})
    assert "no free video port" in action and level == state.ERROR


# --- notifications --------------------------------------------------------------------

N = config.parse({}).notifications


def kinds(prev, cur, cfg=N):
    return [k for k, _, _ in notify.decide(prev, cur, cfg)]


def test_connect_and_reconnect():
    assert kinds({"state": "dialing"}, {"state": "streaming"}) == ["connected"]
    assert notify.decide({"state": "grace"}, {"state": "streaming"}, N)[0][1] == "iPad reconnected"


def test_open_the_app_once_after_two_refusals():
    assert kinds({"state": "dialing", "refused": 0}, {"state": "dialing", "refused": 1}) == []
    assert kinds({"state": "dialing", "refused": 1}, {"state": "dialing", "refused": 2}) == ["needs_action"]
    assert kinds({"state": "dialing", "refused": 2}, {"state": "dialing", "refused": 9}) == []


def test_disconnect_only_after_streaming():
    assert kinds({"state": "streaming"}, {"state": "idle"}) == ["disconnected"]
    assert kinds({"state": "grace"}, {"state": "dialing"}) == ["disconnected"]     # grace ran out
    assert kinds({"state": "dialing"}, {"state": "idle"}) == []                    # never connected


def test_problem_once_per_new_problem():
    p = {"state": "problem", "problem": "x"}
    assert kinds({"state": "off"}, p) == ["problem"]
    assert kinds(p, dict(p)) == []


def test_idle_pause_changes_are_not_notified():
    assert kinds({"state": "streaming", "idle": False}, {"state": "streaming", "idle": True}) == []


def test_switches_turn_kinds_off():
    off = config.parse({"notifications": {"connected": False}}).notifications
    assert kinds({"state": "dialing"}, {"state": "streaming"}, off) == []
    none = config.parse({"notifications": {"enabled": False}}).notifications
    assert kinds({"state": "off"}, {"state": "problem", "problem": "x"}, none) == []


# --- live settings -------------------------------------------------------------------

class FakeSession:
    def __init__(self):
        self.restarts = 0
        self.pipeline, self.ended, self.paused = object(), None, True
        self.device_name, self.panel, self.geom = "iPad", (2360, 1640), Geometry(239, 1080, 1568, 1088)

    def restart_pipeline(self):
        self.restarts += 1


class FakeDaemon:
    def __init__(self, session=None):
        self.conn = type("C", (), {"session": session})() if session else None
        self.rebuilds = 0
        self.state, self.refused, self.grace_until = Daemon.CONNECTED, 0, None

    def rebuild_display(self, new_port):
        self.rebuilds += 1
        self.new_port = new_port


class FakeInjector:
    def __init__(self):
        self.cfgs = []

    def reconfigure(self, c):
        self.cfgs.append(c)


def live(raw):
    cfg = config.parse({})
    session, daemon, inj = FakeSession(), None, FakeInjector()
    daemon = FakeDaemon(session)
    held = cfg.display                                   # a component's reference into the config
    how = apply_config(cfg, config.parse(raw), daemon, inj)
    return cfg, held, session, daemon, inj, how


def test_text_size_rebuilds_the_monitor_and_updates_held_references():
    cfg, held, session, daemon, inj, how = live({"display": {"divisor": 1.25}})
    assert daemon.rebuilds == 1 and session.restarts == 0
    assert held.divisor == 1.25                          # same object, new value
    assert "rebuilt" in how


def test_sharpness_restarts_only_the_stream():
    cfg, _, session, daemon, _, how = live({"video": {"qp": 22}})
    assert session.restarts == 1 and daemon.rebuilds == 0 and cfg.video.qp == 22


def test_input_and_timings_apply_live():
    cfg, _, session, daemon, inj, how = live({"input": {"long_press_ms": 700}, "session": {"grace_s": 20}})
    assert session.restarts == 0 and daemon.rebuilds == 0 and how.endswith("applied live")
    assert inj.cfgs[0].long_press_ms == 700 and cfg.session.grace_s == 20


def test_unchanged_file_does_nothing():
    _, _, session, daemon, inj, how = live({})
    assert how == "nothing changed" and not inj.cfgs and daemon.rebuilds == 0


# --- published facts -----------------------------------------------------------------

def test_facts_while_streaming():
    d = FakeDaemon(FakeSession())
    disp = type("D", (), {"connector": "HDMI-A-1"})()
    f = facts(d, disp)
    assert f["state"] == "streaming" and f["monitor"] == [1568, 1088, 239, 1080]
    assert f["device"] == "iPad" and f["idle"] is True and f["connector"] == "HDMI-A-1"


def test_facts_connected_before_hello_reads_as_dialing():
    s = FakeSession()
    s.pipeline = None
    f = facts(FakeDaemon(s), object())
    assert f["state"] == "dialing" and "monitor" not in f


def test_port_change_is_flagged_as_new_port():
    cfg, _, _, daemon, _, _ = live({"display": {"connector": "HDMI-A-2"}})
    assert daemon.rebuilds == 1 and daemon.new_port is True


def test_text_size_alone_keeps_the_port():
    _, _, _, daemon, _, _ = live({"display": {"divisor": 1.25}})
    assert daemon.new_port is False

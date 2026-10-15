# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""State file, its wording, notifications, and live settings (PROJECT_SPEC.md §8.8)."""
import json
import os

import pytest

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
    assert head == "Show Desktop on iPad (1568 × 1088)"
    assert action == "paused while nothing changes" and level == state.OK


def test_grace_counts_down():
    head, _, _ = state.describe({"state": "grace", "grace_until": 108.0}, now=100.0)
    assert head == "Connection lost — keeping the screen for 8 s"


def test_problem_shows_the_reason():
    _, action, level = state.describe({"state": "problem", "problem": "This computer has no free video port."})
    assert "no free video port" in action and level == state.ERROR


# --- notifications --------------------------------------------------------------------

N = config.parse({"notifications": {"connected": True, "disconnected": True}}).notifications   # every kind on


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


def test_defaults_only_notify_when_the_user_must_act():
    quiet = config.parse({}).notifications
    assert kinds({"state": "dialing"}, {"state": "streaming"}, quiet) == []
    assert kinds({"state": "streaming"}, {"state": "idle"}, quiet) == []
    assert kinds({"state": "dialing", "refused": 1}, {"state": "dialing", "refused": 2}, quiet) == ["needs_action"]
    assert kinds({"state": "off"}, {"state": "problem", "problem": "x"}, quiet) == ["problem"]


# --- exact wording and edge cases (mutation testing found these unasserted) -----------


@pytest.mark.parametrize("facts,expected", [
    ({}, ("Off", "OpenDisplay for X11 is not running.", state.NEUTRAL)),
    ({"state": "off"}, ("Off", "OpenDisplay for X11 is not running.", state.NEUTRAL)),
    ({"state": "problem"}, ("Setup needed", "Something is not set up.", state.ERROR)),
    ({"state": "problem", "problem": "No free video port."}, ("Setup needed", "No free video port.", state.ERROR)),
    ({"state": "idle"}, ("Waiting for your iPad", "Plug it in with a USB cable.", state.WAITING)),
    ({"state": "dialing"}, ("iPad connected", "Unlock it and open the OpenDisplay app.", state.ATTENTION)),
    ({"state": "streaming"}, ("Show Desktop on your iPad", "", state.OK)),
    ({"state": "streaming", "device": "iPad Air", "monitor": [1568, 1088]},
     ("Show Desktop on iPad Air (1568 × 1088)", "", state.OK)),
    ({"state": "streaming", "monitor": [1568]}, ("Show Desktop on your iPad", "", state.OK)),     # malformed size
    ({"state": "streaming", "monitor": "1568x1088"}, ("Show Desktop on your iPad", "", state.OK)),
    ({"state": "streaming", "idle": True}, ("Show Desktop on your iPad", "paused while nothing changes", state.OK)),
    ({"state": "grace"}, ("Connection lost — keeping the screen for a moment", "Reopen the OpenDisplay app to resume.",
                          state.ATTENTION)),
    ({"state": "paused"}, ("Turned off in display settings", "Unplug and replug the iPad to resume.", state.NEUTRAL)),
    ({"state": "martian"}, ("Unknown state", "", state.NEUTRAL)),
])
def test_exact_words_for_every_state(facts, expected):
    assert state.describe(facts) == expected


@pytest.mark.parametrize("until,now,secs", [(110.4, 100.0, 10), (100.6, 100.0, 1), (95.0, 100.0, 0)])
def test_grace_seconds_round_and_never_go_negative(until, now, secs):
    head, _, _ = state.describe({"state": "grace", "grace_until": until}, now=now)
    assert head == f"Connection lost — keeping the screen for {secs} s"


def test_grace_uses_the_clock_when_no_time_is_given(monkeypatch):
    monkeypatch.setattr(state.time, "time", lambda: 1000.0)
    assert state.describe({"state": "grace", "grace_until": 1007})[0] == "Connection lost — keeping the screen for 7 s"


def test_liveness_check_sends_no_signal(monkeypatch):
    sent = []
    monkeypatch.setattr(state.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert state._alive(4242) is True
    assert sent == [(4242, 0)]              # signal 0 only checks; anything else would signal the process


@pytest.mark.parametrize("error,alive", [(ProcessLookupError, False), (PermissionError, True)])
def test_liveness_when_the_process_is_gone_or_not_ours(monkeypatch, error, alive):
    def kill(pid, sig):
        raise error()
    monkeypatch.setattr(state.os, "kill", kill)
    assert state._alive(4242) is alive


@pytest.mark.parametrize("content", ["[1, 2]", '{"pid": 1}', '"streaming"'])
def test_well_formed_json_without_a_state_reads_as_off(tmp_path, content):
    p = tmp_path / "state.json"
    p.write_text(content)
    assert state.read(str(p)) == {"state": state.OFF}


def test_live_daemon_facts_are_returned_as_written(tmp_path):
    p = tmp_path / "state.json"
    facts = {"state": "streaming", "pid": os.getpid(), "device": "iPad Air"}
    p.write_text(json.dumps(facts))
    assert state.read(str(p)) == facts


def test_facts_without_a_pid_are_trusted(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"state": "idle"}))
    assert state.read(str(p)) == {"state": "idle"}


def test_path_follows_xdg_runtime_dir(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1234")
    assert state.path() == "/run/user/1234/odx11/state.json"


def test_path_without_xdg_runtime_dir_is_private_to_the_user(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(state.tempfile, "gettempdir", lambda: "/fake-tmp")
    assert state.path() == f"/fake-tmp/odx11-{os.getuid()}/odx11/state.json"


def test_written_file_is_sorted_json_in_a_private_folder(tmp_path):
    folder = tmp_path / "odx11"
    f = state.StateFile(str(folder / "state.json"), clock=lambda: 7.0)
    f.write({"state": "idle", "device": "iPad"})
    assert (folder / "state.json").read_text() == '{"device": "iPad", "state": "idle", "updated": 7.0}'
    assert folder.stat().st_mode & 0o777 == 0o700


def test_failed_write_leaves_no_temporary_file(tmp_path, monkeypatch):
    f = state.StateFile(str(tmp_path / "state.json"))
    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(state.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        f.write({"state": "idle"})
    assert list(tmp_path.iterdir()) == []


def test_temporary_file_is_hidden_and_beside_the_state_file(tmp_path, monkeypatch):
    seen = {}
    real = state.tempfile.mkstemp
    def spy(**kw):
        seen.update(kw)
        return real(**kw)
    monkeypatch.setattr(state.tempfile, "mkstemp", spy)
    state.StateFile(str(tmp_path / "state.json")).write({"state": "idle"})
    assert seen == {"dir": str(tmp_path), "prefix": ".state-"}


def test_clear_forgets_the_last_write(tmp_path):
    f = state.StateFile(str(tmp_path / "state.json"))
    f.write({"state": "idle"})
    f.clear()
    assert f.write({"state": "idle"})        # written again, not skipped as unchanged
    f.clear()
    f.clear()                                # a second clear with no file is fine

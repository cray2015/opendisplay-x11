# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""The setup checklist and pairing, with the external programs replaced by fakes."""
import subprocess
from types import SimpleNamespace

import pytest

from odx11 import checks


def drm(tmp_path, **ports):
    for name, (status, edid) in ports.items():
        d = tmp_path / f"card1-{name}"
        d.mkdir()
        (d / "status").write_text(status)
        (d / "edid").write_bytes(b"\0" * edid)
    return str(tmp_path)


class Runner:
    """Stands in for subprocess.run: answers by program name, records every call."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        a = self.answers[cmd[0] + (" " + cmd[1] if len(cmd) > 1 and cmd[0] == "idevicepair" else "")]
        if isinstance(a, BaseException):
            raise a
        rc, out, err = a
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


@pytest.fixture
def run(monkeypatch):
    def install(**answers):
        r = Runner(**answers)
        monkeypatch.setattr(checks.subprocess, "run", r)
        return r
    return install


# --- session, port, permission ---------------------------------------------------------

@pytest.mark.parametrize("env,ok", [
    ({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, True),
    ({"XDG_SESSION_TYPE": "x11"}, False),                 # no display
    ({"XDG_SESSION_TYPE": "x11", "DISPLAY": ""}, False),
    ({"DISPLAY": ":0"}, False),                           # not an X11 session
])
def test_session_needs_x11_and_a_display(env, ok):
    c = checks.session(env)
    assert c.ok is ok
    assert c.detail == ("" if ok else "OpenDisplay for X11 needs an X11 session; Wayland is not supported.")


def test_a_named_port_that_does_not_exist(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("disconnected", 0)})
    c = checks.video_port("HDMI-A-7", root)
    assert (c.ok, c.detail) == (False, "There is no HDMI-A-7 on this computer.")


def test_a_free_port_is_named(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-2": ("disconnected", 0)})
    c = checks.video_port("auto", root)
    assert (c.ok, c.detail) == (True, "Using HDMI-A-2.")


def test_permission_waits_for_a_port(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("connected", 256)})
    c = checks.permission("auto", root)
    assert (c.ok, c.detail) == (None, "Needs a free port first.")


def test_writable_port_passes(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("disconnected", 0)})
    assert checks.permission("auto", root).ok is True


def test_group_lookup_names_the_current_groups():
    assert isinstance(checks._groups(), set) and checks._groups()


# --- encoder ------------------------------------------------------------------------

@pytest.mark.parametrize("answer,ok", [
    ((0, b"", b""), True), ((1, b"", b"No such element"), False),
    (FileNotFoundError(), False), (subprocess.TimeoutExpired("gst-inspect-1.0", 10), False),
])
def test_encoder_check(run, answer, ok):
    r = run(**{"gst-inspect-1.0": answer})
    c = checks.encoder(timeout=3)
    assert c.ok is ok
    assert r.calls[0][0] == ["gst-inspect-1.0", "vaapih264enc"] and r.calls[0][1]["timeout"] == 3
    assert c.detail == ("" if ok else "No VA-API H.264 encoder: the graphics driver's video support is missing.")


# --- iPad pairing ---------------------------------------------------------------------

def test_ipad_tools_missing(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda name: None)
    c = checks.ipad()
    assert (c.ok, c.detail) == (None, "iPad tools are not installed.")


@pytest.mark.parametrize("ids", [(0, "", ""), FileNotFoundError(), subprocess.TimeoutExpired("idevice_id", 10)])
def test_no_ipad_attached(run, monkeypatch, ids):
    monkeypatch.setattr(checks.shutil, "which", lambda name: "/usr/bin/" + name)
    run(idevice_id=ids)
    c = checks.ipad()
    assert (c.ok, c.detail) == (None, "Plug in the iPad to check.")


@pytest.mark.parametrize("validate,ok", [
    ((0, "", ""), True), ((1, "", "not paired"), False), (subprocess.TimeoutExpired("idevicepair", 10), False),
])
def test_attached_ipad_pairing_state(run, monkeypatch, validate, ok):
    monkeypatch.setattr(checks.shutil, "which", lambda name: "/usr/bin/" + name)
    r = run(idevice_id=(0, "00008103-ABC\n", ""), **{"idevicepair validate": validate})
    c = checks.ipad(timeout=4)
    assert c.ok is ok
    assert c.detail == ("" if ok else "Unlock the iPad and press Pair, then tap Trust on the iPad.")
    assert [call[0] for call in r.calls] == [["idevice_id", "-l"], ["idevicepair", "validate"]]
    assert all(call[1]["timeout"] == 4 for call in r.calls)


TRUST = "Tap Trust on the iPad, then press Pair again."
UNLOCK = "Unlock the iPad, then press Pair again."


@pytest.mark.parametrize("answer,result", [
    ((0, "SUCCESS: Paired with device", ""), (True, "Paired.")),
    ((1, "", "ERROR: Please accept the trust dialog on the screen of device"), (False, TRUST)),
    ((1, "", "ERROR: user denied the TRUST request"), (False, TRUST)),
    ((1, "", "ERROR: Could not validate with device because a passcode is set"), (False, UNLOCK)),
    ((1, "device is Locked", ""), (False, UNLOCK)),
    ((1, "", "ERROR: something else"), (False, "ERROR: something else")),
    ((1, "", ""), (False, "Pairing failed.")),
])
def test_pair_explains_each_outcome(run, answer, result):
    r = run(**{"idevicepair pair": answer})
    assert checks.pair(timeout=9) == result
    assert r.calls[0][0] == ["idevicepair", "pair"] and r.calls[0][1]["timeout"] == 9


def test_pair_that_cannot_run(run):
    run(**{"idevicepair pair": FileNotFoundError("idevicepair")})
    ok, message = checks.pair()
    assert not ok and message == "Pairing did not finish: idevicepair"


def test_app_check_wording():
    assert checks.app({"state": "grace"}).ok is True
    c = checks.app({})
    assert (c.ok, c.detail, c.link) == (None, "Scan the code with the iPad's camera to get it (TestFlight).",
                                         checks.TESTFLIGHT)

# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""The command line: every subcommand, with the X server, GStreamer, systemd and the
daemon replaced by fakes (the pipeline and window modules are never imported for real)."""
import json
import sys
import types

import pytest

import odx11
from odx11 import __main__ as cli
from odx11 import config, display, upgrade, usbmux


@pytest.fixture
def cfgfile(tmp_path):
    def write(text=""):
        p = tmp_path / "config.toml"
        p.write_text(text)
        return str(p)
    return write


@pytest.fixture
def systemctl(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    return calls


def fake_module(monkeypatch, name, **attrs):
    mod = types.ModuleType(f"odx11.{name}")
    mod.__dict__.update(attrs)
    monkeypatch.setitem(sys.modules, f"odx11.{name}", mod)
    monkeypatch.setattr(odx11, name, mod, raising=False)
    return mod


# --- list ------------------------------------------------------------------------------

def test_list_prints_each_device(monkeypatch, cfgfile, capsys):
    monkeypatch.setattr(usbmux, "list_devices", lambda path, timeout=None: [
        {"DeviceID": 3, "Properties": {"SerialNumber": "00008103-ABC", "ConnectionType": "USB"}}])
    cli.main(["--config", cfgfile(), "list"])
    assert capsys.readouterr().out == "3\t00008103-ABC\tUSB\n"


def test_list_says_when_nothing_is_attached(monkeypatch, cfgfile, capsys):
    seen = []
    monkeypatch.setattr(usbmux, "list_devices", lambda path, timeout=None: seen.append((path, timeout)) or [])
    cli.main(["--config", cfgfile('[transport]\nusbmuxd_socket = "/run/test-mux"\nusbmuxd_timeout_s = 2.5\n'), "list"])
    assert capsys.readouterr().out == "no USB iOS devices attached\n"
    assert seen == [("/run/test-mux", 2.5)]                # a stuck usbmuxd cannot hang `list`


# --- autostart -------------------------------------------------------------------------

def test_autostart_hands_over_the_display_and_starts_the_service(monkeypatch, cfgfile, systemctl):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
    cli.main(["--config", cfgfile(), "autostart"])
    assert systemctl == [["systemctl", "--user", "import-environment", "DISPLAY", "XAUTHORITY"],
                         ["systemctl", "--user", "--no-block", "start", "odx11.service"]]


def test_autostart_without_a_display_only_starts(monkeypatch, cfgfile, systemctl):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    cli.main(["--config", cfgfile(), "autostart"])
    assert systemctl == [["systemctl", "--user", "--no-block", "start", "odx11.service"]]


def test_autostart_respects_the_off_switch(monkeypatch, cfgfile, systemctl):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("XAUTHORITY", raising=False)
    cli.main(["--config", cfgfile("[service]\nenabled = false\n"), "autostart"])
    assert systemctl == [["systemctl", "--user", "import-environment", "DISPLAY"]]


def test_a_broken_config_never_fails_the_login(monkeypatch, cfgfile, systemctl):
    monkeypatch.setenv("DISPLAY", ":0")
    cli.main(["--config", cfgfile("[nope]\n"), "autostart"])           # no exception, no start
    assert ["systemctl", "--user", "--no-block", "start", "odx11.service"] not in systemctl


# --- release ---------------------------------------------------------------------------

@pytest.mark.parametrize("released", [[], ["HDMI-A-1"]])
def test_release_unforces_and_never_needs_the_config(monkeypatch, cfgfile, caplog, released):
    monkeypatch.setattr(display, "release_forced", lambda: released)
    caplog.set_level("INFO")
    cli.main(["--config", cfgfile("[broken\n"), "release"])            # config error tolerated
    assert ("released forced connector(s): HDMI-A-1" in caplog.text) == bool(released)


# --- settings window -------------------------------------------------------------------

def test_settings_opens_the_window_with_any_config_error(monkeypatch, cfgfile):
    got = []
    fake_module(monkeypatch, "gui", main=lambda path, error: got.append((path, error)) or 0)
    path = cfgfile("[nope]\n")
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", path, "settings"])
    assert e.value.code == 0
    assert got == [(path, "unknown config sections/keys: ['nope']")]


def test_settings_with_a_good_config_passes_no_error(monkeypatch, cfgfile):
    got = []
    fake_module(monkeypatch, "gui", main=lambda path, error: got.append(error) or 0)
    with pytest.raises(SystemExit):
        cli.main(["--config", cfgfile(), "settings"])
    assert got == [None]


# --- run / daemon ----------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["run", "daemon", "list"])
def test_a_broken_config_stops_the_service_with_ex_config(cfgfile, cmd):
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", cfgfile("[nope]\n"), cmd])
    assert e.value.code == cli.EX_CONFIG == 78


@pytest.fixture
def service(monkeypatch, tmp_path):
    """Fakes for everything cmd_run touches; returns what it saw."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    seen = {"teardown": 0, "atexit": [], "run": None, "pipelines": []}

    class VDisplay:
        def __init__(self, cfg):
            seen["vdisplay_cfg"] = cfg
        def teardown(self):
            seen["teardown"] += 1

    def run(cfg, vdisplay, injector, make_pipeline, tcp, udid, config_path):
        seen["run"] = dict(tcp=tcp, udid=udid, config_path=config_path)
        make_pipeline((0, 0, 16, 16), "on_au", "on_error", fps=30)
        return seen.get("upgrade", False)

    class Pipeline:
        def __init__(self, geom, video, xname, on_au, on_error, fps):
            seen["pipelines"].append((geom, xname, on_au, on_error, fps))

    order = seen.setdefault("order", [])
    monkeypatch.setattr(display, "connector_problem", lambda d: order.append("port check") or seen.get("problem"))
    monkeypatch.setattr(display, "VirtualDisplay", VDisplay)
    monkeypatch.setattr(cli.atexit, "register", lambda fn: seen["atexit"].append(fn))
    fake_module(monkeypatch, "input", Injector=lambda cfg: "injector")
    fake_module(monkeypatch, "pipeline", Pipeline=Pipeline, init=lambda: order.append("gstreamer"))
    fake_module(monkeypatch, "daemon", run=run, display_name=lambda: ":7")
    return seen


def test_run_wires_the_daemon_together(service, cfgfile):
    path = cfgfile()
    cli.main(["--config", path, "run", "--udid", "00008103-ABC", "--tcp", "127.0.0.1:9000"])
    assert service["run"] == {"tcp": ("127.0.0.1", 9000), "udid": "00008103-ABC", "config_path": path}
    assert service["pipelines"] == [((0, 0, 16, 16), ":7", "on_au", "on_error", 30)]
    assert len(service["atexit"]) == 1 and service["teardown"] == 0       # teardown left to exit
    assert service["order"] == ["gstreamer", "port check"]                  # GStreamer first, as before


def test_run_without_tcp_dials_usb(service, cfgfile):
    cli.main(["--config", cfgfile(), "daemon"])
    assert service["run"]["tcp"] is None and service["run"]["udid"] is None


def test_an_upgrade_tears_down_now_and_exits_for_a_restart(service, cfgfile):
    service["upgrade"] = True
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", cfgfile(), "daemon"])
    assert e.value.code == upgrade.EX_RESTART
    assert service["teardown"] == 1


def test_a_setup_problem_is_published_and_stops_with_ex_config(service, cfgfile, monkeypatch, tmp_path):
    service["problem"] = "No free video port."
    sent = []
    fake_module(monkeypatch, "notify", decide=__import__("odx11.notify", fromlist=["decide"]).decide,
                Notifier=lambda: types.SimpleNamespace(send=lambda *a: sent.append(a)))
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", cfgfile(), "daemon"])
    assert e.value.code == cli.EX_CONFIG
    facts = json.loads((tmp_path / "run" / "odx11" / "state.json").read_text())
    assert (facts["state"], facts["problem"]) == ("problem", "No free video port.")
    assert sent == [("problem", "Setup needed", "No free video port.")]
    with pytest.raises(SystemExit):                       # the same problem again: no second notification
        cli.main(["--config", cfgfile(), "daemon"])
    assert len(sent) == 1


def test_publishing_a_problem_survives_an_unwritable_state_folder(monkeypatch, caplog):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/proc/no-such-dir")
    cli.publish_problem(None, "No free video port.")      # no exception, no notification without a config
    assert "could not write the state file" in caplog.text


def test_verbose_is_accepted_after_the_subcommand(monkeypatch, cfgfile, capsys):
    monkeypatch.setattr(usbmux, "list_devices", lambda path, timeout=None: [])
    cli.main(["--config", cfgfile(), "list", "-v"])
    assert "no USB iOS devices attached" in capsys.readouterr().out


def test_config_is_the_validated_one(monkeypatch, cfgfile):
    seen = []
    monkeypatch.setattr(usbmux, "list_devices", lambda path, timeout=None: [])
    monkeypatch.setattr(config, "load", lambda path: seen.append(path) or config.parse({}))
    cli.main(["--config", "/x/config.toml", "list"])
    assert seen == ["/x/config.toml"]

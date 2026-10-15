# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings window logic: choices, ports, canonical config writing, checklist."""
import os
import tomllib

import pytest

from odx11 import checks, config, settings


def roundtrip(cfg):
    return config.parse(tomllib.loads(settings.to_toml(cfg)))


def test_defaults_write_an_empty_file_with_a_header():
    text = settings.to_toml(config.parse({}))
    assert text.startswith("# OpenDisplay for X11 settings") and "[" not in text


def test_only_changed_values_are_written_and_read_back_equal():
    cfg = config.parse({"display": {"divisor": 1.25, "connector": "HDMI-A-2"}, "video": {"qp": 22},
                        "service": {"enabled": False}, "notifications": {"connected": False},
                        "devices": {"ABC-1": {"side": "below", "mode": [1360, 1024]}}})
    text = settings.to_toml(cfg)
    assert "fps" not in text and "[input]" not in text
    assert roundtrip(cfg) == cfg


def test_save_is_atomic_and_valid(tmp_path):
    p = tmp_path / "odx11" / "config.toml"
    cfg = config.parse({"video": {"qp": 30}})
    settings.save(cfg, str(p))
    assert config.load(str(p)) == cfg
    assert [f.name for f in p.parent.iterdir()] == ["config.toml"]


def test_every_choice_is_accepted_by_the_daemon():
    for d in settings.TEXT_SIZES:
        config.parse({"display": {"divisor": d}})
    for qp in settings.SHARPNESS_QP:
        config.parse({"video": {"qp": qp}})
    for v, _ in settings.FRAME_RATES:
        config.parse({"video": {"fps": v}})
    for v, _ in settings.IDLE_PAUSES:
        config.parse({"video": {"idle_pause_s": v}})
    for v, _ in settings.LONG_PRESSES:
        config.parse({"input": {"long_press_ms": v}})
    for v, _ in settings.GRACES:
        config.parse({"session": {"grace_s": v}})


def test_text_size_shows_clean_resolutions():
    assert settings.resolution((2360, 1640), 1.5) == (1568, 1088)
    assert settings.resolution((1640, 2360), 1.5) == (1568, 1088)     # portrait panel, same mode
    for d in settings.TEXT_SIZES:
        w, h = settings.resolution(settings.DEFAULT_PANEL, d)
        assert w % 16 == 0 and h % 16 == 0


def test_nearest_snaps_hand_edited_values():
    assert settings.nearest(settings.TEXT_SIZES, 1.4) == 2              # 1.5
    assert settings.nearest(settings.GRACES, 12) == 1                   # 10 s


def test_port_labels():
    ports = [("HDMI-A-1", "ours"), ("HDMI-A-2", "free"), ("HDMI-A-3", "monitor")]
    assert settings.port_choices(ports) == [
        ("auto", "Automatic (HDMI-A-1)"),
        ("HDMI-A-1", "HDMI-A-1 — in use by OpenDisplay"),
        ("HDMI-A-2", "HDMI-A-2 — free"),
        ("HDMI-A-3", "HDMI-A-3 — has a monitor")]
    assert settings.port_choices([("HDMI-A-3", "monitor")])[0] == ("auto", "Automatic (no free port)")


# --- checklist ------------------------------------------------------------------------

def drm(tmp_path, **ports):
    for name, (status, edid) in ports.items():
        d = tmp_path / f"card1-{name}"
        d.mkdir()
        (d / "status").write_text(status)
        (d / "edid").write_bytes(b"\0" * edid)
    return str(tmp_path)


def test_session_check():
    assert checks.session({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}).ok
    assert not checks.session({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}).ok


def test_port_check(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("disconnected", 0), "HDMI-A-3": ("connected", 256)})
    assert checks.video_port("auto", root).ok
    bad = checks.video_port("HDMI-A-3", root)
    assert bad.ok is False and "monitor" in bad.detail


def test_no_port_links_the_explanation(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-3": ("connected", 256)})
    c = checks.video_port("auto", root)
    assert c.ok is False and c.link == checks.WHY_PORT


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anything")
def test_permission_check_names_the_group(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("disconnected", 0)})
    os.chmod(tmp_path / "card1-HDMI-A-1" / "status", 0o444)
    c = checks.permission("auto", root, groups={"cray"})
    assert c.ok is False and "plugdev" in c.detail
    c = checks.permission("auto", root, groups={"plugdev"})
    assert c.ok is False and "Restart" in c.detail


def test_app_check_is_proven_by_a_connection():
    assert checks.app({"state": "streaming"}).ok is True
    assert checks.app({"state": "dialing"}).ok is None


def test_version_is_the_same_everywhere():
    """pyproject.toml, the Debian changelog and the AppStream release list must agree."""
    import re
    root = os.path.join(os.path.dirname(__file__), "..")
    py = tomllib.load(open(os.path.join(root, "pyproject.toml"), "rb"))["project"]["version"]
    deb = re.match(r"\S+ \(([^)]+)\)", open(os.path.join(root, "debian", "changelog")).readline()).group(1)
    meta = open(os.path.join(root, "packaging", "io.github.cray2015.OpenDisplayX11.metainfo.xml")).read()
    newest = re.search(r'<release version="([^"]+)"', meta).group(1)
    assert py == deb == newest


# --- edges found by mutation testing ---------------------------------------------------

def test_auto_prefers_the_port_already_in_use_over_a_free_one():
    labels = dict(settings.port_choices([("HDMI-A-1", "free"), ("HDMI-A-2", "ours")]))
    assert labels["auto"] == "Automatic (HDMI-A-2)"
    labels = dict(settings.port_choices([("HDMI-A-1", "monitor"), ("HDMI-A-2", "free")]))
    assert labels["auto"] == "Automatic (HDMI-A-2)"


@pytest.mark.parametrize("value,toml", [
    (True, "true"), (False, "false"), (3, "3"), (1.5, "1.5"),
    ('a "b" \\c', '"a \\"b\\" \\\\c"'),
    ([1568, 1088], "[1568, 1088]"), (("x", True), '["x", true]'),
])
def test_toml_values_are_exact(value, toml):
    assert settings._toml_value(value) == toml


def test_unwritable_value_is_named():
    with pytest.raises(TypeError) as e:
        settings._toml_value({"a": 1})
    assert str(e.value) == "cannot write {'a': 1} to TOML"


def test_devices_are_written_after_the_sections_and_none_is_skipped():
    cfg = config.parse({"video": {"preset": "custom"},
                        "devices": {"b": {"side": "left"}, "a": {"side": "below", "align": "end"}}})
    text = settings.to_toml(cfg)
    assert text.index("[video]") < text.index('[devices."a"]') < text.index('[devices."b"]')
    assert "mode" not in text
    assert config.parse(tomllib.loads(text)) == cfg


def test_save_creates_the_folder_and_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "new" / "config.toml"
    cfg = config.parse({"video": {"fps": 30, "preset": "custom"}})
    settings.save(cfg, str(path))
    settings.save(cfg, str(path))                    # the folder already exists: fine
    assert [p.name for p in path.parent.iterdir()] == ["config.toml"]


def test_failed_save_leaves_no_temporary_file(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(settings.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        settings.save(config.parse({}), str(tmp_path / "config.toml"))
    assert list(tmp_path.iterdir()) == []


def test_temporary_config_is_hidden_and_beside_it(tmp_path, monkeypatch):
    seen = {}
    real = settings.tempfile.mkstemp
    def spy(**kw):
        seen.update(kw)
        return real(**kw)
    monkeypatch.setattr(settings.tempfile, "mkstemp", spy)
    settings.save(config.parse({}), str(tmp_path / "config.toml"))
    assert seen == {"dir": str(tmp_path), "prefix": ".config-"}


def test_device_values_equal_to_the_defaults_are_not_written():
    cfg = config.parse({"devices": {"a": {"mode": [1568, 1088], "side": "right"}}})   # right is the default
    text = settings.to_toml(cfg)
    assert "mode = [1568, 1088]" in text and "side" not in text and "align" not in text

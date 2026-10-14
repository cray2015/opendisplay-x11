import os

import pytest

from odx11 import config
from odx11.display import (Geometry, connector_problem, logical_mode, placement, release_forced,
                           status_path, write_status, xrandr_name)


@pytest.mark.parametrize("panel,expected", [
    ((2048, 1536), (1360, 1024)),
    ((2160, 1620), (1440, 1072)),
    ((2360, 1640), (1568, 1088)),      # the target iPad Air 5
])
def test_spec_mode_table(panel, expected):
    assert logical_mode(*panel, 1.5)[:2] == expected


def test_portrait_keeps_landscape_mode_and_rotates_left():
    assert logical_mode(1640, 2360, 1.5) == (1568, 1088, "left")
    assert logical_mode(2360, 1640, 1.5)[2] == "normal"


def test_modes_are_multiples_of_16():
    for div in (1.0, 1.25, 1.5, 2.0):
        w, h, _ = logical_mode(2360, 1640, div)
        assert w % 16 == 0 and h % 16 == 0


def test_override_wins_and_is_landscape():
    assert logical_mode(1640, 2360, 1.5, override=(1088, 1568)) == (1568, 1088, "left")


MAIN = Geometry(0, 0, 1920, 1080)


@pytest.mark.parametrize("side,align,expected", [
    ("right", "start", (1920, 0)),
    ("right", "center", (1920, -4)),
    ("right", "end", (1920, -8)),
    ("left", "start", (-1568, 0)),
    ("below", "start", (0, 1080)),
    ("below", "center", (176, 1080)),
    ("above", "end", (352, -1088)),
])
def test_placement(side, align, expected):
    assert placement(MAIN, 1568, 1088, side, align) == expected


def test_xrandr_name():
    assert xrandr_name("HDMI-A-1") == "HDMI-1"


# --- the connector force switch (§8.7): fake /sys/class/drm trees ------------------

def drm(tmp_path, **ports):
    """ports: name -> (status, edid_bytes). Builds card1-<name>/{status,edid}."""
    for name, (status, edid) in ports.items():
        d = tmp_path / f"card1-{name}"
        d.mkdir()
        (d / "status").write_text(status + "\n")
        (d / "edid").write_bytes(b"\x00" * edid)
    (tmp_path / "card1").mkdir()                       # the card itself, not a connector
    (tmp_path / "renderD128").mkdir()
    return str(tmp_path)


def display_cfg(connector="HDMI-A-1"):
    return config.parse({"display": {"connector": connector}}).display


def test_status_path_finds_the_card(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("disconnected", 0), "DP-1": ("disconnected", 0)})
    assert status_path("HDMI-A-1", root).endswith("card1-HDMI-A-1/status")
    assert status_path("HDMI-A-9", root) is None


def test_write_status_only_accepts_on_and_detect(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("disconnected", 0)})
    write_status("HDMI-A-1", "on", root)
    assert (tmp_path / "card1-HDMI-A-1" / "status").read_text() == "on"
    with pytest.raises(ValueError):
        write_status("HDMI-A-1", "off", root)


def test_no_problem_with_a_free_writable_port(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("disconnected", 0)})
    assert connector_problem(display_cfg(), root) is None


def test_missing_port_is_explained(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("disconnected", 0)})
    assert "no HDMI-A-2" in connector_problem(display_cfg("HDMI-A-2"), root)


def test_real_monitor_on_the_port_is_refused(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("connected", 256)})
    assert "real monitor" in connector_problem(display_cfg(), root)


def test_port_left_forced_by_a_crash_is_fine(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("connected", 0)})
    assert connector_problem(display_cfg(), root) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anything")
def test_unwritable_port_explains_the_group(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("disconnected", 0)})
    os.chmod(tmp_path / "card1-HDMI-A-1" / "status", 0o444)
    assert "plugdev" in connector_problem(display_cfg(), root)


def test_release_unforces_only_connectors_without_edid(tmp_path):
    root = drm(tmp_path, **{"HDMI-A-1": ("connected", 0),      # ours, left forced
                            "HDMI-A-2": ("disconnected", 0),
                            "HDMI-A-3": ("connected", 256)})   # the real main monitor
    assert release_forced(root) == ["HDMI-A-1"]
    assert (tmp_path / "card1-HDMI-A-1" / "status").read_text() == "detect"
    assert (tmp_path / "card1-HDMI-A-3" / "status").read_text().strip() == "connected"

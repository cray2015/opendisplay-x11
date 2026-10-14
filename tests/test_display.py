import pytest

from odx11.display import Geometry, logical_mode, placement, xrandr_name


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

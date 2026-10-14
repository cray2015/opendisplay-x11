# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from odx11.display import Geometry
from odx11.input import (BUTTON_LEFT, BUTTON_RIGHT, WHEEL_DOWN, WHEEL_LEFT, WHEEL_RIGHT, WHEEL_UP,
                         ScrollAccumulator, TouchGesture, to_root)

G = Geometry(1920, 0, 1568, 1088)


@pytest.mark.parametrize("x,y,expected", [
    (0, 0, (1920, 0)),
    (1, 1, (1920 + 1567, 1087)),       # bottom-right pixel stays on the output
    (0.5, 0.5, (1920 + 784, 544)),
    (-0.2, 1.7, (1920, 1087)),         # clamped, never onto the main monitor
])
def test_touch_mapping(x, y, expected):
    assert to_root(x, y, G) == expected


def test_mapping_follows_new_geometry():
    assert to_root(0, 0, Geometry(-1088, 0, 1088, 1568)) == (-1088, 0)


def test_natural_scroll_sign():
    s = ScrollAccumulator(40)
    assert s.feed(0, 40) == [WHEEL_UP]          # fingers down → content down → wheel up
    assert s.feed(0, -80) == [WHEEL_DOWN, WHEEL_DOWN]
    assert s.feed(40, 0) == [WHEEL_LEFT]
    assert s.feed(-40, 0) == [WHEEL_RIGHT]


def test_scroll_accumulates_remainders():
    s = ScrollAccumulator(40)
    assert s.feed(0, 25) == []
    assert s.feed(0, 25) == [WHEEL_UP]
    assert s.feed(0, 29) == []
    assert s.feed(0, 1) == [WHEEL_UP]


def gesture():
    return TouchGesture(slop_px=8, long_press_s=0.5)


def buttons(acts):
    return [a for a in acts if a[0] != "move"]


def test_tap_clicks_left_on_lift_not_on_touch():
    g = gesture()
    assert buttons(g.feed("began", (100, 100), 0.0)) == []
    acts = g.feed("ended", (101, 100), 0.1)
    assert buttons(acts) == [("press", BUTTON_LEFT), ("release", BUTTON_LEFT)]
    assert acts[0] == ("move", (100, 100))              # clicks where the finger landed


def test_jitter_within_slop_is_still_a_tap():
    g = gesture()
    g.feed("began", (100, 100), 0.0)
    assert buttons(g.feed("moved", (105, 104), 0.05)) == []
    assert buttons(g.feed("ended", (105, 104), 0.1))[0] == ("press", BUTTON_LEFT)


def test_long_press_right_clicks():
    g = gesture()
    g.feed("began", (100, 100), 0.0)
    g.feed("moved", (102, 101), 0.3)
    assert buttons(g.feed("ended", (102, 101), 0.6)) == [("press", BUTTON_RIGHT), ("release", BUTTON_RIGHT)]


def test_drag_presses_at_start_then_moves_then_releases():
    g = gesture()
    g.feed("began", (100, 100), 0.0)
    acts = g.feed("moved", (120, 100), 0.1)
    assert acts == [("move", (100, 100)), ("press", BUTTON_LEFT), ("move", (120, 100))]
    assert buttons(g.feed("moved", (200, 150), 0.2)) == []
    assert buttons(g.feed("ended", (200, 150), 0.3)) == [("release", BUTTON_LEFT)]


def test_long_hold_then_drag_is_a_drag_not_right_click():
    g = gesture()
    g.feed("began", (100, 100), 0.0)
    g.feed("moved", (150, 100), 1.0)
    assert buttons(g.feed("ended", (150, 100), 1.2)) == [("release", BUTTON_LEFT)]


def test_cancelled_before_moving_never_clicks():
    """A two-finger scroll starts as began + cancelled; it must not click under finger one."""
    g = gesture()
    g.feed("began", (100, 100), 0.0)
    assert buttons(g.feed("cancelled", (100, 100), 0.05)) == []
    assert buttons(g.feed("ended", (100, 100), 0.1)) == []    # stray end after cancel: nothing


def test_cancelled_mid_drag_releases():
    g = gesture()
    g.feed("began", (100, 100), 0.0)
    g.feed("moved", (150, 100), 0.1)
    assert buttons(g.feed("cancelled", (150, 100), 0.2)) == [("release", BUTTON_LEFT)]


def test_reset_releases_only_a_held_button():
    g = gesture()
    assert g.reset() == []
    g.feed("began", (100, 100), 0.0)
    g.feed("moved", (150, 100), 0.1)
    assert g.reset() == [("release", BUTTON_LEFT)]


def test_new_touch_during_drag_closes_the_old_one():
    g = gesture()
    g.feed("began", (100, 100), 0.0)
    g.feed("moved", (150, 100), 0.1)
    assert g.feed("began", (300, 300), 0.2)[0] == ("release", BUTTON_LEFT)

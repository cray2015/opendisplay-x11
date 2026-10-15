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


# --- edges found by mutation testing ---------------------------------------------------

def test_values_past_the_right_edge_clamp_to_it():
    assert to_root(1.5, 0.5, G) == (1920 + 1567, 544)


def test_horizontal_scroll_keeps_its_remainder():
    s = ScrollAccumulator(step=40)
    assert s.feed(25, 0) == []
    assert s.feed(25, 0) == [WHEEL_LEFT]             # 25 + 25 = 50: one click, 10 kept
    assert s.feed(-50, 0) == [WHEEL_RIGHT]           # 10 - 50 = -40: one click the other way


def test_holding_exactly_the_long_press_time_right_clicks():
    g = gesture()
    g.feed("began", (10, 10), 0.0)
    assert buttons(g.feed("ended", (10, 10), 0.5)) == [("press", BUTTON_RIGHT), ("release", BUTTON_RIGHT)]


def test_a_long_press_is_timed_from_the_touch_not_from_zero():
    g = gesture()
    g.feed("began", (10, 10), 100.0)
    assert buttons(g.feed("ended", (10, 10), 100.2)) == [("press", BUTTON_LEFT), ("release", BUTTON_LEFT)]


def test_moving_exactly_the_slop_is_still_a_tap():
    g = gesture()
    g.feed("began", (10, 10), 0.0)
    assert buttons(g.feed("moved", (18, 10), 0.1)) == []           # 8 px = slop: not yet a drag
    assert buttons(g.feed("ended", (18, 10), 0.2)) == [("press", BUTTON_LEFT), ("release", BUTTON_LEFT)]


@pytest.mark.parametrize("end", ["ended", "cancelled"])
def test_after_a_touch_ends_moves_do_nothing(end):
    g = gesture()
    g.feed("began", (10, 10), 0.0)
    g.feed(end, (10, 10), 0.1)
    assert buttons(g.feed("moved", (300, 300), 0.2)) == []
    assert buttons(g.feed("ended", (300, 300), 0.3)) == []


def test_after_a_reset_moves_do_nothing():
    g = gesture()
    g.feed("began", (10, 10), 0.0)
    g.reset()
    assert buttons(g.feed("moved", (300, 300), 0.1)) == []
    assert buttons(g.feed("ended", (300, 300), 0.2)) == []


def test_a_fresh_gesture_ignores_stray_moves_and_ends():
    g = gesture()
    assert buttons(g.feed("moved", (300, 300), 0.1)) == []
    assert buttons(g.feed("ended", (300, 300), 0.2)) == []
    assert g.reset() == []


def test_scrolling_two_steps_right_at_once_gives_two_clicks():
    s = ScrollAccumulator(step=40)
    assert s.feed(-90, 0) == [WHEEL_RIGHT, WHEEL_RIGHT]
    assert s.feed(-30, 0) == [WHEEL_RIGHT]           # -10 kept, -10 - 30 = -40


@pytest.mark.parametrize("end", ["ended", "cancelled"])
def test_a_finished_touch_returns_to_idle(end):
    g = gesture()
    assert g.state == TouchGesture.IDLE
    g.feed("began", (10, 10), 0.0)
    g.feed(end, (10, 10), 0.1)
    assert g.state == TouchGesture.IDLE
    g.feed("began", (10, 10), 0.2)
    g.reset()
    assert g.state == TouchGesture.IDLE

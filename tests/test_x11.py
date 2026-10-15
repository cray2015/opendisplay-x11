# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""The X11 side against a real (virtual) X server: the idle gate's window, damage, pointer,
key and cursor tracking (activity.py) and touch injection (input.py).

These move the pointer, synthesise clicks and create windows, so they run only where
ODX11_X11_TESTS=1 is set: CI's Xvfb job, or locally inside a container with Xvfb. Never on a
desktop someone is using."""
import os
import select
import time

import pytest

if os.environ.get("ODX11_X11_TESTS") != "1":
    pytest.skip("X11 tests need a throwaway X server (ODX11_X11_TESTS=1)", allow_module_level=True)

from Xlib import X, Xatom, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

from odx11 import config  # noqa: E402
from odx11.activity import ActivityMonitor  # noqa: E402
from odx11.display import Geometry  # noqa: E402
from odx11.input import BUTTON_LEFT, BUTTON_RIGHT, WHEEL_DOWN, WHEEL_UP, Injector  # noqa: E402

REGION = (100, 100, 400, 300)             # the "virtual monitor" area of the X screen


@pytest.fixture
def x():
    d = display.Display()
    yield d
    xtest.fake_input(d, X.MotionNotify, x=1500, y=900)   # park the pointer far away
    # Closing the connection destroys the windows it created (the X server's default close-down mode).
    d.sync()
    d.close()


@pytest.fixture
def mon(x):
    m = ActivityMonitor()
    m.set_region(REGION)
    yield m
    m.close()


def window(x, rect, events=0):
    w = x.screen().root.create_window(*rect, 0, x.screen().root_depth, X.InputOutput,
                                      X.CopyFromParent, background_pixel=x.screen().white_pixel,
                                      event_mask=events)
    w.map()
    x.sync()
    return w


def draw(x, w):
    gc = w.create_gc(foreground=x.screen().black_pixel)
    w.fill_rectangle(gc, 0, 0, 10, 10)
    x.sync()


def collect(mon, timeout=0.5):
    """Drain the monitor until the X server has gone quiet; merge what it reported."""
    seen = {"wake": False, "pointer": None, "cursor": False, "pressed": False}
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        select.select([mon.fileno()], [], [], max(0, end - time.monotonic()))
        a = mon.drain()
        seen["wake"] |= a.wake
        seen["pressed"] |= a.pressed
        seen["cursor"] |= a.cursor_changed
        if a.pointer is not None:
            seen["pointer"] = a.pointer
    return seen


def move(x, px, py):
    xtest.fake_input(x, X.MotionNotify, x=px, y=py)
    x.sync()


# --- windows and damage -------------------------------------------------------------------

def test_a_window_appearing_on_the_monitor_wakes_capture(x, mon):
    window(x, (150, 150, 50, 50))
    assert collect(mon)["wake"]


def test_a_window_appearing_elsewhere_does_not(x, mon):
    window(x, (900, 700, 50, 50))
    assert not collect(mon)["wake"]


def test_drawing_wakes_only_when_the_window_overlaps(x, mon):
    inside, outside = window(x, (150, 150, 50, 50)), window(x, (900, 700, 50, 50))
    collect(mon)
    draw(x, outside)
    assert not collect(mon)["wake"]
    draw(x, inside)
    assert collect(mon)["wake"]


def test_moving_a_window_onto_or_off_the_monitor_wakes(x, mon):
    w = window(x, (900, 700, 50, 50))
    collect(mon)
    w.configure(x=150, y=150)
    x.sync()
    assert collect(mon)["wake"]                            # moved onto it
    w.configure(x=1000, y=700)
    x.sync()
    assert collect(mon)["wake"]                            # moved off: the area must repaint


def test_closing_a_window_on_the_monitor_wakes(x, mon):
    w = window(x, (150, 150, 50, 50))
    collect(mon)
    w.unmap()
    x.sync()
    assert collect(mon)["wake"]


def test_a_minimized_window_drawing_does_not_wake(x, mon):
    w = window(x, (150, 150, 50, 50))
    collect(mon)
    wm_state = x.intern_atom("WM_STATE")
    w.change_property(wm_state, wm_state, 32, [3, 0])     # IconicState
    x.sync()
    collect(mon)
    draw(x, w)
    assert not collect(mon)["wake"]


def test_a_hidden_window_drawing_does_not_wake(x, mon):
    w = window(x, (150, 150, 50, 50))
    collect(mon)
    w.change_property(x.intern_atom("_NET_WM_STATE"), Xatom.ATOM, 32, [x.intern_atom("_NET_WM_STATE_HIDDEN")])
    x.sync()
    collect(mon)
    draw(x, w)
    assert not collect(mon)["wake"]


def test_windows_that_exist_at_start_are_tracked(x):
    w = window(x, (150, 150, 50, 50))                      # before the monitor starts
    m = ActivityMonitor()
    m.set_region(REGION)
    collect(m)
    draw(x, w)
    assert collect(m)["wake"]
    m.close()


# --- pointer, keys and cursor --------------------------------------------------------------

def test_pointer_motion_over_the_monitor_is_reported_and_wakes(x, mon):
    move(x, 1500, 900)
    collect(mon)
    move(x, 200, 200)
    seen = collect(mon)
    assert seen["pointer"] == (200, 200) and seen["wake"]
    assert mon.pointer_position() == (200, 200)


def test_leaving_the_monitor_wakes_once_then_motion_elsewhere_does_not(x, mon):
    move(x, 200, 200)
    collect(mon)
    move(x, 900, 700)
    assert collect(mon)["wake"]                            # repaint without the cursor
    move(x, 950, 720)
    seen = collect(mon)
    assert seen["pointer"] == (950, 720) and not seen["wake"]


def test_with_the_cursor_channel_motion_alone_never_wakes(x, mon):
    mon.draw_cursor = False
    move(x, 1500, 900)
    collect(mon)
    move(x, 200, 200)
    seen = collect(mon)
    assert seen["pointer"] == (200, 200) and not seen["wake"]


@pytest.mark.parametrize("where,pressed", [((200, 200), True), ((900, 700), False)])
def test_a_key_press_counts_only_over_the_monitor(x, mon, where, pressed):
    move(x, *where)
    collect(mon)
    keycode = x.keysym_to_keycode(0xFFE9)                  # Alt_L
    xtest.fake_input(x, X.KeyPress, keycode)
    xtest.fake_input(x, X.KeyRelease, keycode)
    x.sync()
    assert collect(mon)["pressed"] is pressed


def test_a_click_over_the_monitor_counts_as_a_press(x, mon):
    move(x, 200, 200)
    collect(mon)
    xtest.fake_input(x, X.ButtonPress, 1)
    xtest.fake_input(x, X.ButtonRelease, 1)
    x.sync()
    assert collect(mon)["pressed"]


def test_a_cursor_shape_change_is_reported_with_its_image(x, mon):
    w = window(x, (150, 150, 100, 100))
    move(x, 200, 200)
    collect(mon)
    font = x.open_font("cursor")
    cursor = font.create_glyph_cursor(font, 152, 153, (0, 0, 0), (65535, 65535, 65535))   # XC_xterm
    w.change_attributes(cursor=cursor)
    x.sync()
    assert collect(mon)["cursor"]
    width, height, xhot, yhot, pixels, serial = mon.cursor_image()
    assert width > 0 and height > 0 and len(pixels) == width * height


# --- touch injection -----------------------------------------------------------------------

class Clicks:
    """A window covering the monitor area that records the button events it receives."""

    def __init__(self, x):
        self.x = x
        self.w = window(x, REGION, X.ButtonPressMask | X.ButtonReleaseMask)

    def events(self, timeout=0.5):
        out, end = [], time.monotonic() + timeout
        while time.monotonic() < end:
            select.select([self.x.fileno()], [], [], max(0, end - time.monotonic()))
            while self.x.pending_events():
                e = self.x.next_event()
                if e.type in (X.ButtonPress, X.ButtonRelease):
                    out.append(("press" if e.type == X.ButtonPress else "release", e.detail,
                                e.root_x, e.root_y))
        return out


@pytest.fixture
def inj():
    clock = {"t": 0.0}
    i = Injector(config.parse({}).input, clock=lambda: clock["t"])
    i.set_geometry(Geometry(*REGION))
    i.clock_box = clock
    yield i
    i.x.close()


def touch(inj, phase, x, y, t=None):
    if t is not None:
        inj.clock_box["t"] = t
    inj.handle({"type": "touch", "phase": phase, "x": x, "y": y})


def test_a_tap_clicks_left_where_the_finger_lifted(x, inj):
    c = Clicks(x)
    touch(inj, "began", 0.25, 0.5, t=0.0)
    touch(inj, "ended", 0.25, 0.5, t=0.1)
    assert c.events() == [("press", BUTTON_LEFT, 200, 250), ("release", BUTTON_LEFT, 200, 250)]


def test_a_long_press_clicks_right(x, inj):
    c = Clicks(x)
    touch(inj, "began", 0.5, 0.5, t=0.0)
    touch(inj, "ended", 0.5, 0.5, t=0.6)
    assert [e[:2] for e in c.events()] == [("press", BUTTON_RIGHT), ("release", BUTTON_RIGHT)]


def test_a_drag_presses_where_it_started_and_releases_where_it_ended(x, inj):
    c = Clicks(x)
    touch(inj, "began", 0.25, 0.25, t=0.0)
    touch(inj, "moved", 0.75, 0.75, t=0.1)
    touch(inj, "ended", 0.75, 0.75, t=0.2)
    ev = c.events()
    assert ev[0][:2] == ("press", BUTTON_LEFT) and ev[0][2:] == (200, 175)
    assert ev[-1][:2] == ("release", BUTTON_LEFT) and ev[-1][2:] == (399, 324)   # 0.75 × 399 rounds to 299
    assert inj.x.screen().root.query_pointer()._data["root_x"] == 399


def test_a_session_ending_mid_drag_releases_the_button(x, inj):
    c = Clicks(x)
    touch(inj, "began", 0.25, 0.25, t=0.0)
    touch(inj, "moved", 0.75, 0.75, t=0.1)
    inj.release_all()
    assert c.events()[-1][:2] == ("release", BUTTON_LEFT)


def test_scrolling_turns_into_wheel_clicks(x, inj):
    c = Clicks(x)
    inj.handle({"type": "touch", "phase": "began", "x": 0.5, "y": 0.5})
    inj.handle({"type": "touch", "phase": "cancelled", "x": 0.5, "y": 0.5})   # two-finger scroll starts
    inj.handle({"type": "scroll", "dx": 0, "dy": 80})
    inj.handle({"type": "scroll", "dx": 0, "dy": -40})
    assert [e[:2] for e in c.events()] == [("press", WHEEL_UP), ("release", WHEEL_UP)] * 2 + \
        [("press", WHEEL_DOWN), ("release", WHEEL_DOWN)]


def test_malformed_or_early_messages_do_nothing(x, inj):
    c = Clicks(x)
    inj.handle({"type": "touch", "phase": "began", "x": "0.5", "y": 0.5})
    inj.handle({"type": "scroll", "dx": None, "dy": 40})
    inj.set_geometry(None)
    touch(inj, "began", 0.5, 0.5)
    touch(inj, "ended", 0.5, 0.5)
    assert c.events() == []


def test_new_input_settings_apply_live(x, inj):
    c = Clicks(x)
    inj.reconfigure(config.parse({"input": {"scroll_step_px": 10, "tap_slop_px": 0, "long_press_ms": 100}}).input)
    touch(inj, "began", 0.5, 0.5, t=0.0)                   # a scroll always follows a touch,
    touch(inj, "cancelled", 0.5, 0.5, t=0.0)               # which puts the pointer on the monitor
    inj.handle({"type": "scroll", "dx": 0, "dy": 20})
    assert [e[1] for e in c.events() if e[0] == "press"] == [WHEEL_UP, WHEEL_UP]
    touch(inj, "began", 0.5, 0.5, t=0.0)
    touch(inj, "ended", 0.5, 0.5, t=0.15)
    assert [e[1] for e in c.events() if e[0] == "press"] == [BUTTON_RIGHT]

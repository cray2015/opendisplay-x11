# SPDX-FileCopyrightText: 2026 vibhanshu
# SPDX-License-Identifier: GPL-3.0-or-later
from odx11.activity import Tracker

VIRT = (239, 1080, 1568, 1088)          # the iPad monitor, below the main one
MAIN_WIN = (0, 0, 1920, 1048)            # a maximised terminal on the main monitor
IPAD_WIN = (532, 1178, 820, 596)


def tracker():
    t = Tracker()
    t.set_region(VIRT)
    return t


def test_busy_window_on_main_monitor_never_wakes():
    t = tracker()
    t.mapped(1, MAIN_WIN)
    assert not any(t.damaged(1) for _ in range(100))


def test_damage_on_the_ipad_monitor_wakes():
    t = tracker()
    t.mapped(2, IPAD_WIN)
    assert t.damaged(2)


def test_window_straddling_both_monitors_wakes():
    t = tracker()
    t.mapped(3, (100, 900, 800, 400))
    assert t.damaged(3)


def test_moving_onto_or_off_the_ipad_wakes_both_ways():
    t = tracker()
    t.mapped(4, MAIN_WIN)
    assert t.configured(4, IPAD_WIN)        # dragged onto the iPad
    assert t.configured(4, MAIN_WIN)        # and back: the iPad must repaint without it


def test_map_and_unmap_on_the_ipad_wake():
    t = tracker()
    assert t.mapped(5, IPAD_WIN)
    assert t.unmapped(5)
    assert not t.unmapped(5)                # already gone


def test_unknown_or_unviewable_windows_do_not_wake():
    t = tracker()
    assert not t.damaged(99) and not t.configured(99, IPAD_WIN)


def test_pointer_wakes_inside_and_once_on_leaving():
    t = tracker()
    assert not t.pointer(100, 100)          # main monitor
    assert t.pointer(600, 1500)             # over the iPad
    assert t.pointer(100, 100)              # just left: repaint without the cursor
    assert not t.pointer(120, 100)


def test_no_session_no_wakes():
    t = Tracker()
    t.mapped(6, IPAD_WIN)
    assert not t.damaged(6) and not t.pointer(600, 1500)


def test_region_change_applies_to_existing_windows():
    t = tracker()
    t.mapped(7, MAIN_WIN)
    assert not t.damaged(7)
    t.set_region((1920, 0, 1568, 1088))     # monitor moved right of main
    t.mapped(8, (2000, 100, 400, 400))
    assert t.damaged(8) and not t.damaged(7)

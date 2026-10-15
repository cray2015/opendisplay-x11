# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""The virtual monitor against a real X server with RandR modes: Xorg's dummy driver, whose
spare output DUMMY1 stands in for the forced HDMI port (DUMMY0 is the main monitor).

Real: every xrandr call, RandR queries and events, cvt, placement, mode add/resize/remove.
Faked: only the kernel side, i.e. the sysfs force switch and the "connected" state it causes.
Not covered: rotation (the dummy driver cannot rotate) — portrait is verified by hand.

Runs only where ODX11_X11_TESTS=1 and the X server has DUMMY outputs (tests/x11/)."""
import os
import subprocess

import pytest

if os.environ.get("ODX11_X11_TESTS") != "1":
    pytest.skip("X11 tests need a throwaway X server (ODX11_X11_TESTS=1)", allow_module_level=True)
if b"DUMMY1" not in subprocess.run(["xrandr"], capture_output=True).stdout:
    pytest.skip("needs Xorg's dummy driver (DUMMY0/DUMMY1 outputs)", allow_module_level=True)

from odx11 import config, display  # noqa: E402
from odx11.display import Geometry  # noqa: E402
from odx11.protocol import DecodeLimits  # noqa: E402

DEVICE = config.DeviceConfig()                 # right of the main monitor, top-aligned


def xrandr():
    return subprocess.run(["xrandr"], capture_output=True, text=True, check=True).stdout


def modes():
    return {line.split()[0] for line in xrandr().splitlines() if line.startswith("   ")}


@pytest.fixture
def vd(monkeypatch):
    """A VirtualDisplay on DUMMY1, with the kernel's force switch simulated."""
    forced = []
    monkeypatch.setattr(display, "resolve_connector", lambda setting, root=display.SYSFS_DRM: "HDMI-A-1")
    monkeypatch.setattr(display, "xrandr_name", lambda kernel: "DUMMY1")

    def force(self, state):
        forced.append(state)
        self._fake_connected = state == "on"
    monkeypatch.setattr(display.VirtualDisplay, "_force", force)
    monkeypatch.setattr(display.VirtualDisplay, "_connected", lambda self: getattr(self, "_fake_connected", False))
    cfg = config.parse({"display": {"connect_timeout_s": 1, "settle_timeout_s": 0.2}}).display
    v = display.VirtualDisplay(cfg)
    v.forced_log = forced
    yield v
    v.teardown()
    subprocess.run(["xrandr", "--output", "DUMMY1", "--off"], capture_output=True)
    for m in modes():
        if m.startswith("odx11_"):
            subprocess.run(["xrandr", "--delmode", "DUMMY1", m], capture_output=True)
            subprocess.run(["xrandr", "--rmmode", m], capture_output=True)


def test_cvt_gives_a_reduced_blanking_modeline():
    line = display.cvt_modeline(1568, 1088, 60)
    assert line[1:5] == ["1568", "1616", "1648", "1728"] and line[5] == "1088"


def test_main_monitor_is_the_primary_output(vd):
    assert vd.main_geometry() == Geometry(0, 0, 1024, 768)


def test_first_attach_forces_the_port_and_places_the_monitor_right_of_main(vd):
    g = vd.attach(2360, 1640, DEVICE)
    assert g == Geometry(1024, 0, 1568, 1088)              # panel / 1.5, right of DUMMY0, top-aligned
    assert vd.forced_log == ["on"] and vd.forced and vd.attached
    assert "odx11_1568x1088" in modes()
    assert "DUMMY1 connected 1568x1088+1024+0" in xrandr()
    assert vd.geometry() == g


@pytest.mark.parametrize("side,align,pos", [("below", "start", (0, 768)), ("right", "start", (1024, 0))])
def test_placement_follows_the_device_settings_when_nothing_was_saved(vd, side, align, pos):
    g = vd.attach(2360, 1640, config.DeviceConfig(side=side, align=align))
    assert (g.x, g.y, g.w, g.h) == (*pos, 1568, 1088)


def test_attaching_again_changes_nothing(vd):
    first = vd.attach(2360, 1640, DEVICE)
    before = xrandr()
    assert vd.attach(2360, 1640, DEVICE) == first
    assert xrandr() == before and vd.forced_log == ["on"]   # not forced twice


def test_a_new_text_size_resizes_in_place_and_drops_the_old_mode(vd):
    vd.attach(2360, 1640, DEVICE)
    vd.cfg.divisor = 2.0
    w, h, _ = display.logical_mode(2360, 1640, 2.0)
    g = vd.attach(2360, 1640, DEVICE)
    assert (g.x, g.y, g.w, g.h) == (1024, 0, w, h)          # same place, new size
    assert f"odx11_{w}x{h}" in modes() and "odx11_1568x1088" not in modes()


def test_a_decode_limit_shrinks_the_mode(vd):
    g = vd.attach(2360, 1640, DEVICE, limits=DecodeLimits(1280, 1280))
    assert g.w <= 1280 and g.h <= 1280 and abs(g.w / g.h - 1568 / 1088) < 0.02


def test_teardown_unforces_switches_off_and_removes_the_mode(vd):
    vd.attach(2360, 1640, DEVICE)
    vd.teardown()
    assert vd.forced_log == ["on", "detect"] and not vd.forced and not vd.attached
    assert "odx11_1568x1088" not in modes()
    assert "DUMMY1 connected 1568" not in xrandr()
    vd.teardown()                                          # idempotent
    assert vd.forced_log == ["on", "detect"]


def test_teardown_of_a_display_never_set_up_touches_nothing(vd):
    before = xrandr()
    vd.teardown()
    assert vd.forced_log == [] and xrandr() == before


def test_a_port_that_never_connects_is_an_error_and_still_unforced(vd, monkeypatch):
    monkeypatch.setattr(display.VirtualDisplay, "_connected", lambda self: False)
    with pytest.raises(RuntimeError, match="DUMMY1 did not become connected"):
        vd.attach(2360, 1640, DEVICE)
    assert vd.forced                                       # marked before the write: teardown unforces
    vd.teardown()
    assert vd.forced_log == ["on", "detect"]


def test_a_monitor_turned_off_elsewhere_has_no_geometry_and_raises_an_event(vd):
    vd.attach(2360, 1640, DEVICE)
    vd.drain_events()
    subprocess.run(["xrandr", "--output", "DUMMY1", "--off"], check=True)
    import select
    select.select([vd.fileno()], [], [], 1)
    assert vd.drain_events() is True                       # RandR told us
    assert vd.geometry() is None
    assert vd.drain_events() is False                      # nothing new


def test_an_explicit_main_output_that_is_off_is_an_error(vd):
    vd.cfg.main_output = "DUMMY2"
    with pytest.raises(RuntimeError, match="main output DUMMY2 is not enabled"):
        vd.main_geometry()
    vd.cfg.main_output = "DUMMY99"
    with pytest.raises(RuntimeError, match="xrandr has no output DUMMY99"):
        vd.main_geometry()

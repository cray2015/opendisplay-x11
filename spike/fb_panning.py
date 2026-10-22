#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""--fb / --panning experiment: can an off-screen strip of the X canvas act as the iPad's screen?
Runs inside the dummy-driver Xorg container only. Checks, per setup:
  pointer  - does XTest absolute motion (what odx11/input.py sends) land inside the strip?
  pan      - does the main monitor's view move (pan) when the pointer goes there?
  capture  - can a window drawn in the strip be read back (what ximagesrc does)?
Spike for PROJECT_SPEC.md §11 (no-free-port software region), 2026-10-22. Throwaway; not product
code. It resizes the screen and moves the pointer, so it refuses any display but the container's:
  docker run --rm --network=none --memory=1g --cpus=2 -v "$PWD/spike:/src:ro" odx11-x11 \\
    python3 -I fb_panning.py
"""
import os
import subprocess
import sys

if os.environ.get("DISPLAY") != ":9":
    sys.exit("fb_panning.py: runs only inside the odx11-x11 container (DISPLAY=:9)")

from Xlib import X, display
from Xlib.ext import randr, xtest

MAIN_W, MAIN_H = 1024, 768           # the dummy server's main monitor
STRIP_W, STRIP_H = 1568, 1088          # an iPad panel, as in the headless work
FB_W, FB_H = MAIN_W + STRIP_W, max(MAIN_H, STRIP_H)
TARGET = (MAIN_W + 700, 500)           # a point well inside the strip

d = display.Display()
root = d.screen().root


def xr(*args, quiet=False):
    r = subprocess.run(["xrandr", *args], capture_output=True, text=True)
    if not quiet:
        print(f"  $ xrandr {' '.join(args)}" + (f"  -> rc={r.returncode} {r.stderr.strip()}" if r.returncode else ""))
    return r


def crtc_pos(name="DUMMY0"):
    """Where the monitor's view actually sits on the canvas (the CRTC), not the panning area."""
    res = randr.get_screen_resources(root)
    for o in res.outputs:
        info = randr.get_output_info(root, o, res.config_timestamp)
        if info.name == name and info.crtc:
            c = randr.get_crtc_info(root, info.crtc, res.config_timestamp)
            return f"{c.width}x{c.height}+{c.x}+{c.y}"
    return "off"


def panning_line(name="DUMMY0"):
    out, mine = [], False
    for line in xr("--verbose", quiet=True).stdout.splitlines():
        if not line.startswith(" ") and not line.startswith("\t"):
            mine = line.startswith(name + " ")
        elif mine and line.strip().split(":")[0] in ("Panning", "Tracking", "Border"):
            out.append(line.strip())
    return "; ".join(out) or "none"


def move(x, y):
    xtest.fake_input(d, X.MotionNotify, x=x, y=y)
    d.sync()
    p = root.query_pointer()
    return p.root_x, p.root_y


def capture_test():
    """Map a solid-colour window in the strip, read the pixels back from the root window."""
    win = root.create_window(MAIN_W + 100, 100, 200, 200, 0, d.screen().root_depth,
                             X.InputOutput, X.CopyFromParent,
                             background_pixel=0x00FF8800, override_redirect=True)
    win.map()
    win.clear_area()
    d.sync()
    img = root.get_image(MAIN_W + 150, 150, 1, 1, X.ZPixmap, 0xFFFFFFFF)
    win.destroy()
    d.sync()
    px = img.data[:4]
    return "ok (orange read back)" if px[:3] == b"\x00\x88\xff" else f"FAIL pixel={px.hex()}"


def report(label):
    g = root.get_geometry()
    got = move(*TARGET)
    inside = got[0] >= MAIN_W
    print(f"  canvas {g.width}x{g.height}; DUMMY0 at {crtc_pos()}; {panning_line()}")
    print(f"  pointer: asked {TARGET}, got {got} -> {'IN STRIP' if inside else 'blocked'}")
    print(f"  main view after move: {crtc_pos()}")
    print(f"  capture: {capture_test()}")
    edge = move(MAIN_W - 1, 300)
    print(f"  pointer at main monitor's right edge {edge}: main view {crtc_pos()}")
    move(200, 200)
    print(f"  main view after pointer returns: {crtc_pos()}")


def reset():
    xr("--output", "DUMMY0", "--panning", "0x0", quiet=True)
    xr("--output", "DUMMY1", "--off", quiet=True)
    xr("--output", "DUMMY0", "--mode", f"{MAIN_W}x{MAIN_H}", "--pos", "0x0", quiet=True)
    xr("--fb", f"{MAIN_W}x{MAIN_H}", quiet=True)
    move(200, 200)


print("== setup: one monitor DUMMY0 at", MAIN_W, "x", MAIN_H)
xr("--output", "DUMMY0", "--mode", f"{MAIN_W}x{MAIN_H}", "--pos", "0x0")
xr("--output", "DUMMY1", "--off")
print(xr("--listmonitors", quiet=True).stdout)

print("== A: canvas only (--fb), no panning")
xr("--fb", f"{FB_W}x{FB_H}")
report("A")
reset()

print("\n== B: --fb + panning over the whole canvas (tracking defaults to the panning area)")
xr("--fb", f"{FB_W}x{FB_H}")
xr("--output", "DUMMY0", "--panning", f"{FB_W}x{FB_H}+0+0")
report("B")
reset()

print("\n== C: --fb + panning over the whole canvas, tracking only the main monitor's area")
xr("--fb", f"{FB_W}x{FB_H}")
xr("--output", "DUMMY0", "--panning", f"{FB_W}x{FB_H}+0+0/{MAIN_W}x{MAIN_H}+0+0")
report("C")

print("\n== C+: also declare the strip as a RandR 1.5 monitor with no output (what a WM could read)")
xr("--setmonitor", "IPAD", f"{STRIP_W}/196x{STRIP_H}/147+{MAIN_W}+0", "none")
print(xr("--listmonitors", quiet=True).stdout)

print("== C++: does a later RandR change (another output turning on/off) undo it?")
xr("--addmode", "DUMMY1", "1024x768", quiet=True)
xr("--output", "DUMMY1", "--mode", "1024x768", "--pos", f"{FB_W}x0")
xr("--output", "DUMMY1", "--off")
print(f"  canvas {root.get_geometry().width}x{root.get_geometry().height}; DUMMY0 view {crtc_pos()}; {panning_line()}")
print(xr("--listmonitors", quiet=True).stdout)
reset()

print("\n== D (control): the current product's way: a real second output beside the main one")
xr("--addmode", "DUMMY1", "1024x768", quiet=True)
xr("--output", "DUMMY1", "--mode", "1024x768", "--right-of", "DUMMY0")
got = move(MAIN_W + 500, 300)
print(f"  pointer: asked {(MAIN_W + 500, 300)}, got {got}")
reset()

print("\n== E: --fb + panning area = the main monitor's own size (nothing to pan; only switches panning on)")
xr("--fb", f"{FB_W}x{FB_H}", "--output", "DUMMY0", "--panning", f"{MAIN_W}x{MAIN_H}+0+0")
report("E")
xr("--setmonitor", "IPAD", f"{STRIP_W}/196x{STRIP_H}/147+{MAIN_W}+0", "none")
print(xr("--listmonitors", quiet=True).stdout)
print("  real-mouse-style check: can the pointer also reach dead canvas below the main monitor?")
print(f"  asked (300, {FB_H - 50}), got {move(300, FB_H - 50)}")

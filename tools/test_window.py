#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""A small window that redraws itself at a fixed rate — a repeatable stand-in for
"an app that changes N times a second" in tools/perf_bench.py.

  python3 tools/test_window.py --hz 5 [--x 400 --y 1300] [--size 200x120]

Without --x/--y it sits on the virtual monitor (xrandr HDMI-n with an odx11 mode,
else the first non-primary output). Runs until killed.
"""
import argparse
import re
import subprocess
import time

from Xlib import X, display


def virtual_monitor_origin() -> tuple[int, int]:
    out = subprocess.run(["xrandr", "--query"], capture_output=True, text=True).stdout
    for m in re.finditer(r"^(\S+) connected (primary )?(\d+)x(\d+)\+(\d+)\+(\d+)", out, re.M):
        if not m.group(2):                      # not the primary: the iPad's monitor
            return int(m.group(5)), int(m.group(6))
    raise SystemExit("no secondary monitor found — is the iPad connected?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hz", type=float, required=True)
    ap.add_argument("--x", type=int)
    ap.add_argument("--y", type=int)
    ap.add_argument("--size", default="200x120")
    a = ap.parse_args()
    w, h = map(int, a.size.split("x"))
    if a.x is None or a.y is None:
        ox, oy = virtual_monitor_origin()
        a.x, a.y = ox + 200, oy + 200
    d = display.Display()
    screen = d.screen()
    # override_redirect: placed exactly where asked, no window-manager decoration.
    win = screen.root.create_window(a.x, a.y, w, h, 0, screen.root_depth, X.InputOutput,
                                    X.CopyFromParent, background_pixel=screen.black_pixel,
                                    override_redirect=True)
    win.set_wm_name(f"odx11 test window {a.hz:g} Hz")
    colours = [screen.white_pixel, screen.black_pixel]
    gcs = [win.create_gc(foreground=c) for c in colours]
    win.map()
    d.flush()
    period = 1.0 / a.hz
    nxt, i = time.monotonic(), 0
    while True:
        win.fill_rectangle(gcs[i % 2], 0, 0, w, h)
        d.flush()
        i += 1
        nxt += period
        time.sleep(max(0.0, nxt - time.monotonic()))


if __name__ == "__main__":
    main()

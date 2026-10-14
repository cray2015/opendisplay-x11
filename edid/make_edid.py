#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 vibhanshu
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the virtual monitor's EDID for a logical mode (PROJECT_SPEC.md §6).

  python3 edid/make_edid.py 1568 1088 --name "iPad" --id <hello.id> -o odx11.bin

For the boot-time `drm.edid_firmware=` fallback (PROJECT_SPEC.md §4): the runtime
debugfs override is blocked by Secure Boot lockdown, so the daemon does not use it.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from odx11 import edid  # noqa: E402
from odx11.display import cvt_modeline  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("width", type=int)
    ap.add_argument("height", type=int)
    ap.add_argument("--refresh", type=int, default=60)
    ap.add_argument("--name", default="iPad")
    ap.add_argument("--id", help="receiver hello.id, for a stable per-device serial")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()
    timing = edid.Timing.from_modeline(cvt_modeline(a.width, a.height, a.refresh))
    blob = edid.build(timing, a.name, edid.serial_for(a.id))
    with open(a.output, "wb") as f:
        f.write(blob)
    print(f"wrote {a.output}: {a.width}x{a.height}@{timing.refresh_hz:.2f} "
          f"'{a.name}' serial {edid.serial_for(a.id):08X}")


if __name__ == "__main__":
    main()

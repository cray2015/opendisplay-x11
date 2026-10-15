#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw the README's coverage badge (no badge service): CI publishes it to GitHub Pages.

  python3 tools/coverage_badge.py 82 _site        # writes _site/coverage.svg and _site/index.html
"""
import os
import sys

REPO = "https://github.com/cray2015/opendisplay-x11"


def colour(pct: float) -> str:
    """Shields.io's scale, so it reads like the other badges."""
    for limit, c in ((90, "#4c1"), (80, "#97ca00"), (70, "#a4a61d"), (60, "#dfb317"), (50, "#fe7d37")):
        if pct >= limit:
            return c
    return "#e05d44"


def svg(pct: float) -> str:
    label, value = "coverage", f"{pct:.0f}%"
    lw, vw = 61, 8 + 7 * len(value)           # widths fit Verdana 11 px, like shields.io
    w = lw + vw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="{label}: {value}">
<title>{label}: {value}</title>
<rect width="{w}" height="20" rx="3" fill="#555"/>
<path d="M{lw} 0h{vw - 3}a3 3 0 0 1 3 3v14a3 3 0 0 1-3 3h-{vw - 3}z" fill="{colour(pct)}"/>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
<text x="{lw / 2}" y="15" fill="#010101" fill-opacity=".3">{label}</text><text x="{lw / 2}" y="14">{label}</text>
<text x="{lw + vw / 2}" y="15" fill="#010101" fill-opacity=".3">{value}</text>
<text x="{lw + vw / 2}" y="14">{value}</text>
</g></svg>
"""


def main(pct_text: str, out_dir: str):
    pct = float(pct_text)
    if not 0 <= pct <= 100:
        raise SystemExit(f"coverage must be 0..100, got {pct_text}")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "coverage.svg"), "w") as f:
        f.write(svg(pct))
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={REPO}">'
                f'<title>OpenDisplay for X11</title><a href="{REPO}">OpenDisplay for X11</a>\n')


if __name__ == "__main__":
    main(*sys.argv[1:3])

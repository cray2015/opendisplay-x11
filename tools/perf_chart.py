#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw the README's performance chart from the benchmark CSVs (no plotting library).

  python3 tools/perf_chart.py                 # writes docs/perf/cpu-by-change-rate-{light,dark}.svg

Grouped bars: CPU (% of one core) per "how often the iPad's screen changes", one bar
per performance preset. Light and dark files are separate so the README's <picture>
follows the viewer's GitHub theme. Colours: the dataviz reference palette's first three
categorical slots, validated for colour-vision deficiency in both modes; slot 3 is under
3:1 on the light surface, so every bar carries its value (and the README has the table).
"""
import csv
import math
import os
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERF = os.path.join(ROOT, "docs", "perf")
VERSION = "0.3.8"
SERIES = [("Power saver", f"results-{VERSION}-power_saver.csv"),
          ("Balanced", f"results-{VERSION}-balanced.csv"),
          ("Best quality", f"results-{VERSION}-best_quality.csv")]
CATEGORIES = [("idle", "idle"), ("rate1", "1 / s"), ("rate2", "2 / s"), ("rate5", "5 / s"),
              ("rate10", "10 / s"), ("continuous", "continuous")]
THEMES = {
    "light": {"surface": "#fcfcfb", "text": "#0b0b0b", "muted": "#52514e", "grid": "#e4e3df",
              "axis": "#bdbcb6", "series": ["#2a78d6", "#eb6834", "#1baf7a"]},
    "dark": {"surface": "#1a1a19", "text": "#f0efec", "muted": "#c3c2b7", "grid": "#33332f",
             "axis": "#5c5b55", "series": ["#3987e5", "#d95926", "#199e70"]},
}
W, H = 760, 380
LEFT, RIGHT, TOP, BOTTOM = 64, 24, 92, 56
Y_STEP = 10
BAR_W, PAIR_GAP, RADIUS = 24, 2, 4


def load(name):
    by = {}
    with open(os.path.join(PERF, name)) as f:
        for r in csv.DictReader(f):
            by.setdefault(r["scenario"], []).append(float(r["cpu_pct_one_core"]))
    return {k: statistics.mean(v) for k, v in by.items()}


def bar_path(x, y, w, h, r):
    """A bar with rounded data end (top) and a square baseline (bottom)."""
    r = min(r, w / 2, h)
    return (f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z")


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")


def render(theme):
    t = THEMES[theme]
    data = [load(f) for _, f in SERIES]
    Y_MAX = Y_STEP * math.ceil(max(v for d in data for v in d.values()) / Y_STEP)
    plot_w, plot_h = W - LEFT - RIGHT, H - TOP - BOTTOM
    y_of = lambda v: TOP + plot_h * (1 - min(v, Y_MAX) / Y_MAX)
    font = "font-family=\"-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif\""
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'role="img" aria-labelledby="t d">',
           f'<title id="t">CPU use by how often the iPad\'s screen changes, per performance preset ({VERSION})</title>',
           '<desc id="d">Grouped bars of CPU, percent of one core, for each change rate; '
           'the README table has every value.</desc>',
           f'<rect width="{W}" height="{H}" rx="8" fill="{t["surface"]}"/>',
           f'<text x="{LEFT}" y="30" {font} font-size="16" font-weight="600" fill="{t["text"]}">'
           'CPU use by how often the iPad\'s screen changes</text>',
           f'<text x="{LEFT}" y="50" {font} font-size="12" fill="{t["muted"]}">'
           f'% of one CPU core · lower is better · mean of 3 runs · i5-6500T, HD 530 · version {VERSION}</text>']
    # legend only when there are two or more series (a single series is named by the title)
    lx = LEFT
    for i, (name, _) in enumerate(SERIES if len(SERIES) > 1 else []):
        out.append(f'<rect x="{lx}" y="64" width="12" height="12" rx="3" fill="{t["series"][i]}"/>')
        out.append(f'<text x="{lx + 18}" y="74" {font} font-size="12" fill="{t["text"]}">{esc(name)}</text>')
        lx += 18 + 8 * len(name) + 24
    # grid + y axis labels (recessive)
    for v in range(0, Y_MAX + 1, Y_STEP):
        y = y_of(v)
        colour = t["axis"] if v == 0 else t["grid"]
        out.append(f'<line x1="{LEFT}" y1="{y:.1f}" x2="{W - RIGHT}" y2="{y:.1f}" stroke="{colour}" stroke-width="1"/>')
        out.append(f'<text x="{LEFT - 10}" y="{y + 4:.1f}" {font} font-size="11" text-anchor="end" '
                   f'fill="{t["muted"]}">{v}%</text>')
    # bars
    group_w = plot_w / len(CATEGORIES)
    pair_w = len(SERIES) * BAR_W + (len(SERIES) - 1) * PAIR_GAP
    for c, (key, label) in enumerate(CATEGORIES):
        gx = LEFT + c * group_w + (group_w - pair_w) / 2
        for s, (name, _) in enumerate(SERIES):
            v = data[s].get(key)
            if v is None:
                continue
            x = gx + s * (BAR_W + PAIR_GAP)
            y = y_of(v)
            h = max(1.0, TOP + plot_h - y)
            out.append(f'<path d="{bar_path(x, TOP + plot_h - h, BAR_W, h, RADIUS)}" fill="{t["series"][s]}">'
                       f'<title>{esc(label)}, {esc(name)}: {v:.1f}% of one core</title></path>')
            out.append(f'<text x="{x + BAR_W / 2:.1f}" y="{TOP + plot_h - h - 6:.1f}" {font} font-size="10" '
                       f'text-anchor="middle" fill="{t["text"]}">{v:.1f}</text>')
        out.append(f'<text x="{LEFT + c * group_w + group_w / 2:.1f}" y="{TOP + plot_h + 20:.1f}" {font} '
                   f'font-size="12" text-anchor="middle" fill="{t["muted"]}">{esc(label)}</text>')
    out.append(f'<text x="{LEFT + plot_w / 2:.1f}" y="{H - 12}" {font} font-size="12" text-anchor="middle" '
               f'fill="{t["muted"]}">how often the iPad\'s screen changes</text>')
    out.append("</svg>")
    path = os.path.join(PERF, f"cpu-by-change-rate-{theme}.svg")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    return path


if __name__ == "__main__":
    for theme in THEMES:
        print(render(theme))

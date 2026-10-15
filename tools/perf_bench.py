#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproducible performance runs (README "Performance").

Runs the daemon from this source tree in the foreground against the attached
iPad (the OpenDisplay app open), once per run, with a scenario's settings, and
records the daemon's CPU and the iPad's own stats (PHONE-STATS). The user
service is stopped for the duration and restarted at the end.

  python3 tools/perf_bench.py --scenario continuous --runs 3 --csv docs/perf/results.csv
  python3 tools/perf_bench.py --preset power_saver --scenario rate --hz 5 --csv docs/perf/results-power_saver.csv

Scenarios:
  idle         nothing changing on the iPad's monitor (keep it empty)
  rate         a test window on the iPad's monitor redrawing --hz times a second
               (tools/test_window.py) — read any app off the CPU-vs-rate curve
  light        whatever is on the iPad's monitor now
  continuous   every frame converted and encoded (frame gate and idle pause off): worst case,
               at the preset's frame rate and sharpness
  continuous30 the same at Power saver's 30 fps and sharpness

--preset picks the performance preset (default balanced); every scenario runs with it.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from odx11.config import PRESETS  # noqa: E402

SCENARIOS = {
    "idle": "",
    "rate": "",
    "light": "",
    "continuous": '[video]\npreset = "custom"\nfps = 60\nqp = 26\nidle_pause_s = 0\nchange_tail_ms = 0\n',
    # Power saver's frame rate and sharpness, with pausing off (a preset would set its own pause).
    "continuous30": '[video]\npreset = "custom"\nfps = 30\nqp = 30\nidle_pause_s = 0\nchange_tail_ms = 0\n',
}
STATS = ("fps", "e2e50", "e2e95", "enc50", "stalls")


def cpu_ticks(pid: int) -> int:
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])          # utime + stime


def pss_mb(pid: int) -> float:
    """Proportional set size: the process's own memory plus its share of shared pages."""
    with open(f"/proc/{pid}/smaps_rollup") as f:
        for line in f:
            if line.startswith("Pss:"):
                return round(int(line.split()[1]) / 1024, 1)
    return 0.0


def scenario_config(scenario: str, preset: str) -> str:
    if scenario == "continuous":                 # the preset's rate and sharpness, nothing gated
        p = PRESETS[preset]
        return (f'[video]\npreset = "custom"\nfps = {p["fps"]}\nqp = {p["qp"]}\n'
                'idle_pause_s = 0\nchange_tail_ms = 0\n')
    return SCENARIOS[scenario] or f'[video]\npreset = "{preset}"\n'


def run_once(scenario: str, warmup: float, seconds: float, hz: float | None = None,
             preset: str = "balanced") -> dict:
    cfg = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
    cfg.write(scenario_config(scenario, preset))
    cfg.close()
    log = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False)
    proc = subprocess.Popen([sys.executable, "-m", "odx11", "--config", cfg.name, "run"],
                            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    window = None
    try:
        if scenario == "rate":
            time.sleep(4)                               # the virtual monitor must exist first
            window = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "test_window.py"),
                                       "--hz", str(hz)])
        time.sleep(warmup)
        mark = os.path.getsize(log.name)
        ticks_hz = os.sysconf("SC_CLK_TCK")
        t0, c0 = time.monotonic(), cpu_ticks(proc.pid)
        time.sleep(seconds)
        t1, c1 = time.monotonic(), cpu_ticks(proc.pid)
        mem = pss_mb(proc.pid)
    finally:
        if window is not None:
            window.terminate()
            window.wait(timeout=10)
        proc.send_signal(2)
        proc.wait(timeout=20)
    with open(log.name) as f:
        f.seek(mark)
        text = f.read()
    os.unlink(cfg.name)
    os.unlink(log.name)
    samples = [json.loads(m.replace("'", '"').replace("True", "true").replace("False", "false"))
               for m in re.findall(r"PHONE-STATS (\{.*\})", text)]
    row = {"scenario": scenario if scenario != "rate" else f"rate{hz:g}",
           "cpu_pct_one_core": round(100 * (c1 - c0) / ticks_hz / (t1 - t0), 1),
           "pss_mb": mem, "stats_samples": len(samples)}
    for key in STATS:
        vals = [s[key] for s in samples if isinstance(s.get(key), (int, float))]
        row[key] = round(sum(vals) / len(vals), 1) if vals else ""
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=SCENARIOS, required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--warmup", type=float, default=10)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--hz", type=float, help="redraw rate for --scenario rate")
    ap.add_argument("--preset", choices=PRESETS, default="balanced")
    ap.add_argument("--csv", help="append rows to this CSV file")
    a = ap.parse_args()
    if a.scenario == "rate" and not a.hz:
        ap.error("--scenario rate needs --hz")
    service_was_active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "odx11"]).returncode == 0
    if service_was_active:
        subprocess.run(["systemctl", "--user", "stop", "odx11"], check=True)
        time.sleep(1)
    rows = []
    try:
        for i in range(a.runs):
            row = run_once(a.scenario, a.warmup, a.seconds, a.hz, a.preset)
            row["run"] = i + 1
            rows.append(row)
            print(json.dumps(row), flush=True)
            time.sleep(2)
    finally:
        if service_was_active:
            subprocess.run(["systemctl", "--user", "start", "odx11"])
    if a.csv and rows:
        os.makedirs(os.path.dirname(os.path.abspath(a.csv)), exist_ok=True)
        new = not os.path.exists(a.csv)
        with open(a.csv, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date", "scenario", "run", "cpu_pct_one_core", "pss_mb",
                                              "stats_samples", *STATS])
            if new:
                w.writeheader()
            for r in rows:
                w.writerow({"date": time.strftime("%Y-%m-%d"), **r})


if __name__ == "__main__":
    main()

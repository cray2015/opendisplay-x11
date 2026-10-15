#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproducible performance runs (README "Performance").

Runs the daemon from this source tree in the foreground against the attached
iPad (the OpenDisplay app open), once per run, with a scenario's settings, and
records the daemon's CPU and the iPad's own stats (PHONE-STATS). The user
service is stopped for the duration and restarted at the end.

  python3 tools/perf_bench.py --scenario continuous --runs 3 --csv docs/perf/results.csv

Scenarios:
  idle         nothing changing on the iPad's monitor (keep it empty)
  light        whatever is on the iPad's monitor now (e.g. a system monitor redrawing)
  continuous   every frame converted and encoded (frame gate and idle pause off): worst case
  continuous30 the same at Power saver's 30 fps and sharpness
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
SCENARIOS = {
    "idle": "",
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


def run_once(scenario: str, warmup: float, seconds: float) -> dict:
    cfg = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
    cfg.write(SCENARIOS[scenario])
    cfg.close()
    log = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False)
    proc = subprocess.Popen([sys.executable, "-m", "odx11", "--config", cfg.name, "run"],
                            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    try:
        time.sleep(warmup)
        mark = os.path.getsize(log.name)
        hz = os.sysconf("SC_CLK_TCK")
        t0, c0 = time.monotonic(), cpu_ticks(proc.pid)
        time.sleep(seconds)
        t1, c1 = time.monotonic(), cpu_ticks(proc.pid)
    finally:
        proc.send_signal(2)
        proc.wait(timeout=20)
    with open(log.name) as f:
        f.seek(mark)
        text = f.read()
    os.unlink(cfg.name)
    os.unlink(log.name)
    samples = [json.loads(m.replace("'", '"').replace("True", "true").replace("False", "false"))
               for m in re.findall(r"PHONE-STATS (\{.*\})", text)]
    row = {"scenario": scenario, "cpu_pct_one_core": round(100 * (c1 - c0) / hz / (t1 - t0), 1),
           "stats_samples": len(samples)}
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
    ap.add_argument("--csv", help="append rows to this CSV file")
    a = ap.parse_args()
    service_was_active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "odx11"]).returncode == 0
    if service_was_active:
        subprocess.run(["systemctl", "--user", "stop", "odx11"], check=True)
        time.sleep(1)
    rows = []
    try:
        for i in range(a.runs):
            row = run_once(a.scenario, a.warmup, a.seconds)
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
            w = csv.DictWriter(f, fieldnames=["date", "scenario", "run", "cpu_pct_one_core", "stats_samples", *STATS])
            if new:
                w.writeheader()
            for r in rows:
                w.writerow({"date": time.strftime("%Y-%m-%d"), **r})


if __name__ == "__main__":
    main()

#!/bin/bash
# AC 23: measure the odx11 service's CPU over an idle period (PROJECT_SPEC.md §10, §11).
# Leave the desktop alone while it runs; the iPad stays connected.
#   bash tools/idle_cpu.sh [minutes]        default 10
set -euo pipefail
MIN=${1:-10}
PID=$(systemctl --user show -p MainPID --value odx11.service)
[[ $PID != 0 ]] || { echo "odx11.service is not running" >&2; exit 1; }
ticks() { awk '{print $14 + $15}' "/proc/$PID/stat"; }
HZ=$(getconf CLK_TCK)
t0=$(ticks); s0=$(date +%s)
echo "measuring odx11 (pid $PID) for $MIN min — hands off the desktop..."
sleep $((MIN * 60))
t1=$(ticks); s1=$(date +%s)
pct=$(echo "scale=2; ($t1 - $t0) / $HZ / ($s1 - $s0) * 100" | bc)
echo "odx11 CPU: ${pct}% of one core over $((s1 - s0)) s (budget: 2%)"
journalctl --user -u odx11 --since "@$s0" --no-pager -o cat | grep -cE 'session ending' \
  | xargs -I{} echo "session drops during the test: {}"

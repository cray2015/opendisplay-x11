#!/bin/bash
# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Mutation testing with an X server, so the input injector's mutants are tested too (config:
# [tool.mutmut] in pyproject.toml). Runs as the test user via run.sh. Heavy: each worker holds the
# test suite, so cap it on a desktop (MUTMUT_CHILDREN workers, default 2):
#   docker run --rm --memory=2g --cpus=2 -v "$PWD:/src:ro" odx11-x11 /x11/mutation.sh
# Prints a per-module table (Markdown); exits non-zero only if mutmut itself fails.
set -euo pipefail
python3 -m venv --system-site-packages "$HOME/mut"
"$HOME/mut/bin/pip" install --quiet --disable-pip-version-check mutmut==3.8.0
if ! "$HOME/mut/bin/mutmut" run --max-children "${MUTMUT_CHILDREN:-2}" > mutmut.log 2>&1; then
    tail -50 mutmut.log
    exit 1
fi
"$HOME/mut/bin/mutmut" results --all true > results.txt
python3 - <<'PY'
import collections
import re
c = collections.defaultdict(collections.Counter)
for line in open("results.txt"):
    m = re.match(r"\s*odx11\.(\w+)\.\S+:\s*(\w[\w ]*)", line)
    if m:
        c[m.group(1)][m.group(2).strip()] += 1
total = collections.Counter()
print("| module | mutants | killed | survived | no tests | score |\n|---|---|---|---|---|---|")
for mod, k in sorted(c.items()):
    n, killed = sum(k.values()), k["killed"] + k["timeout"] + k["suspicious"]   # a crash is caught too
    total.update(k)
    print(f"| {mod} | {n} | {killed} | {k['survived']} | {k['no tests']} | {100 * killed / n:.0f} % |")
n, killed = sum(total.values()), total["killed"] + total["timeout"] + total["suspicious"]
print(f"| **total** | {n} | {killed} | {total['survived']} | {total['no tests']} | **{100 * killed / n:.1f} %** |")
PY
grep -E "^    odx11\..*: survived" results.txt || true

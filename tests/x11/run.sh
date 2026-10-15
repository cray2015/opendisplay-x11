#!/bin/bash
# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Starts Xorg (dummy driver) as root, then runs the given command as an ordinary user on a
# writable copy of /src, so tests that check file permissions behave as on a desktop.
set -euo pipefail
Xorg :9 -noreset -config /x11/xorg-dummy.conf -logfile /tmp/xorg.log >/dev/null 2>&1 &
for _ in $(seq 100); do DISPLAY=:9 xrandr >/dev/null 2>&1 && break; sleep 0.1; done
cp -r /src /home/tester/src
chown -R tester:tester /home/tester/src
cd /home/tester/src
exec setpriv --reuid=tester --regid=tester --init-groups env HOME=/home/tester DISPLAY=:9 "$@"

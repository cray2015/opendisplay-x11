<!--
SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
SPDX-License-Identifier: GPL-3.0-or-later
-->
# 0002 — A udev rule is the only privilege; no root code at runtime

- **Status:** Accepted (2026-10-14); supersedes the sudoers root helper of M1–M2 · *Reconstructed 2026-10-15 from PROJECT_SPEC.md §8.7, commits 4d86dcf and b96adbf*
- **Deciders:** the owner (chose `plugdev` after asking what it is used for elsewhere), with Claude

## Context
Forcing the connector (ADR 0001) writes `/sys/class/drm/card*-HDMI-A-*/status`, which is
root-only. M1–M2 used a root helper script allowed by a sudoers rule. The product goal is no
terminal for users: install a .deb and enter the password once.

## Decision
Ship a udev rule that gives the `plugdev` group write access to the HDMI connectors' `status`
attribute (the backlight-brightness pattern). The service writes `on`/`detect` itself as the
desktop user. No root code runs while it runs; the root helper and sudoers rule were deleted.

## Why this is acceptable
Any X client of the same user can already switch monitors on and off with `xrandr`, so writing
a connector's force switch adds no real capability. `DP-n` connectors are never covered. `plugdev`
is the established group for user access to pluggable hardware, and the installing user is in it on
Debian-family systems. Correction found later: only the account the OS installer created, not
accounts added afterwards; the window's setup checklist explains the fix.

## Consequences
- The privileged surface is one 2-line file, reviewable by anyone.
- `odx11 release` (run from `ExecStopPost` and the package's `prerm`) must stay config-independent and only unforce connectors that are connected with no EDID, so it can never switch off a real monitor.
- RPM/Arch builds would need a dedicated group (they have no `plugdev`).

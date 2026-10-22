<!--
SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
SPDX-License-Identifier: GPL-3.0-or-later
-->
# 0001 — The virtual monitor is a spare HDMI connector forced on

- **Status:** Accepted (2026-10-14) · *Reconstructed 2026-10-15 from PROJECT_SPEC.md §2, §4, §8.1 and commit 857f824*
- **Deciders:** the owner, with Claude

## Context
X11 with the `modesetting` driver offers outputs only for kernel connectors: an application
cannot create a monitor out of nothing (Windows has its Indirect Display Driver framework, macOS
`CGVirtualDisplay`, Wayland compositors their own virtual outputs). The development machine has
an empty DP++ port, which i915 exposes both as `DP-n` and as `HDMI-n`.

## Decision
Write `on` to the empty connector's sysfs `status` to force it connected, add a mode sized from
the iPad's panel, and let the desktop extend onto it. Always the `HDMI-A-n` alias (TMDS needs no
handshake), never `DP-n`. Unforce it on every exit path.

## Alternatives rejected
- **EVDI** (DisplayLink's virtual-display module): out-of-tree kernel module, reported black screens after updates.
- **Intel `VIRTUAL` outputs:** need the old xf86-video-intel driver; Mint runs `modesetting`.
- **A DP dummy plug:** the monitor would always exist, even with no iPad: windows land on an invisible screen.
- **A software region (`--fb`/`--setmonitor`):** not a real output; desktops don't treat it as a monitor. *Re-evaluated 2026-10-22:* with `--panning` the pointer does reach it (container spike); whether desktops accept it is open — `PROJECT_SPEC.md` §4, §11, `docs/decisions.md`.

## Consequences
- **Hard requirement:** a free HDMI/DP++ port. Stated first in the README and the package description (the owner asked for this, 2026-10-14).
- Forcing a connector needs write access to a sysfs file, which needs privilege: see ADR 0002.
- Proven on hardware in the M0 spike before any product code was written (the owner's condition: feasibility first).

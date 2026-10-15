<!--
SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
SPDX-License-Identifier: GPL-3.0-or-later
-->
# 0004 — Event-driven, change-gated capture; no polling

- **Status:** Accepted (2026-10-14, extended through 0.3.8) · *Reconstructed 2026-10-15 from PROJECT_SPEC.md §4, §8.8 and commits a34dbdc, fe7f4fa, 614d4ed, 55c4dc0, 7b8e117, 3b24d1d*
- **Deciders:** the owner, with Claude; the owner's hands-on testing on the iPad decided the timing values

## Context
Earlier Cinnamon applets built with Claude were disabled for idle CPU spikes. `ximagesrc` captures
at full frame rate regardless of change, and colour conversion plus encoding cost ~45 % of a core
at 60 fps on the i5-6500T.

## Decision
No polling anywhere: usbmuxd `Listen`, RandR, per-window XDamage, XInput2 and inotify events drive
everything; timers only for ping, grace, redial, debounce and the idle pause. Capture runs, but
frames are converted and encoded only for a short tail after something on the iPad's monitor
changes. Per-window damage is filtered to the virtual monitor (root-window damage reports the whole
screen under Cinnamon's compositor). Keys and clicks over the iPad's monitor also open the gate,
because Cinnamon draws Alt+Tab and its menus without any damage.

## Consequences
- Idle: ~0.1–0.3 % of one core; a window redrawing twice a second costs 3–18 % (by preset and processing) instead of ~40 %.
- Gating is fragile in ways a CPU benchmark cannot see: a 50 ms tail (0.3.4) froze the iPad on menus and was reverted after the owner's report; the cursor-during-grab bug (0.3.6) and Alt+Tab (0.3.7) were found the same way. Rule since then: any gating change needs a hands-on check on the iPad.
- Tail length became part of the performance presets (Power saver 100 ms, Balanced 50 ms, Best quality 150 ms; the owner's call for Best quality).

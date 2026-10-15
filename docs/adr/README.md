<!--
SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
SPDX-License-Identifier: GPL-3.0-or-later
-->
# Architecture decision records

The five decisions that shape this codebase most. **All five are reconstructed**: written
on 2026-10-15 by Claude (the AI assistant used to build the project) from `PROJECT_SPEC.md`,
the git history and the code, not at the time each decision was made. The owner
reviews them; the short log of every decision, including small ones, is
[`docs/decisions.md`](../decisions.md). New significant decisions get a new ADR when they
are made.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-virtual-monitor-forced-hdmi-connector.md) | The virtual monitor is a spare HDMI connector forced on | Accepted |
| [0002](0002-udev-rule-is-the-only-privilege.md) | A udev rule is the only privilege; no root code at runtime | Accepted |
| [0003](0003-no-command-channel-into-the-daemon.md) | No command channel into the background service | Accepted |
| [0004](0004-event-driven-change-gated-capture.md) | Event-driven, change-gated capture; no polling | Accepted |
| [0005](0005-process-frames-on-the-gpu-with-fallback.md) | Process frames on the GPU when possible, fall back to the CPU | Accepted |

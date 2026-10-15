<!--
SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
SPDX-License-Identifier: GPL-3.0-or-later
-->
# Human overrides

> **DRAFT — every entry is waiting for the owner's confirmation.** Each entry is marked
> DRAFT until confirmed; unconfirmed entries are removed.

Places where the owner constrained, redirected or corrected the AI assistant (Claude) that
built most of this code. The **quote** is the owner's own message from the development
session, verbatim, with only spelling left as written. The *what changed* column says what
happened as a result; it does not claim reasons the owner did not give. Dates are UTC.
Drafted by Claude on 2026-10-15 from the session transcript and git history.

| # | Date | The owner said | What changed | Status |
|---|---|---|---|---|
| 1 | 2026-10-14 | "verify and validate before you start working on it. feasibility of the idea needs to be tested before building" | The spec was checked against the hardware first; the M0 spike proved transport, forced connector and encoder before product code (ADR 0001) | DRAFT |
| 2 | 2026-10-14 | "anything configurable should land in a config file." | Every tunable moved to `config.toml`; a test keeps the example file equal to the defaults; a CLAUDE.md rule | DRAFT |
| 3 | 2026-10-14 | "i dont want to add a applet thing this would make it cinnamon specific. an app whereas make it usable on any system which support x11" | The planned Cinnamon panel applet was dropped; a desktop-agnostic GTK window instead | DRAFT |
| 4 | 2026-10-14 | "i think before anything packaging should be decided as that will dictate how installation works?" | Packaging (.deb, udev privilege) was designed before the GUI (ADR 0002) | DRAFT |
| 5 | 2026-10-14 | "the whole agenda is to make it like a windows like experience, so as to not to touch the cli" | No terminal for end users became goal #1: double-click install, password dialogs, everything in the window | DRAFT |
| 6 | 2026-10-14 | "first tell me what is plugdev used for is it commonly used for other things and production application pattern as well?" | The group choice was justified against established uses before it was adopted; a later correction narrowed who is in `plugdev` by default | DRAFT |
| 7 | 2026-10-14 | "then we need to mention this hard constraint at the top of the read me and when the deb package is opened. otherwise it is of no use." | The free-port requirement is now the first thing in the README and the package description | DRAFT |
| 8 | 2026-10-14 | "make it short and link the readme url … try not to put a ton of technical jargon. follow KISS" | The package description was cut to plain words plus a link | DRAFT |
| 9 | 2026-10-14 | "using something in maintain mode is not the best choice in my opinion." → "go with GTK 4 + libadwaita" | The AI's first pick (GTK 3) was replaced by GTK 4 + libadwaita | DRAFT |
| 10 | 2026-10-14 | "priority is to be as silent as possible … I think Utility is better for category" | Notifications only when the user must act; app category Utility | DRAFT |
| 11 | 2026-10-15 | "the freezes are serious and are visible when the mouse is moving right click move freezes and then comes back its bad experience" | The 50 ms change tail shipped as a CPU win (0.3.4) was reverted (0.3.5) | DRAFT |
| 12 | 2026-10-15 | "i think the freeze is because of the cursor. the cursor responds but does not updates it position on the screen maybe a diff bug altogether here." | The owner's diagnosis pointed at the real bug: no raw input events during pointer grabs (XI 2.0), fixed in 0.3.6 | DRAFT |
| 13 | 2026-10-15 | "i saw some slow down when using alt and tab on the ipad. specially when using right and left arrows." | Led to the finding that Cinnamon draws Alt+Tab with no damage; keys and clicks now wake capture (0.3.7) | DRAFT |
| 14 | 2026-10-15 | "maybe 150ms should go to best quality and 50ms in power saver" | The change tail became part of the presets; Best quality keeps 150 ms (Power saver got 100 ms, Balanced 50 ms) | DRAFT |
| 15 | 2026-10-15 | "i think we can reduce the CPU usage if there is a dedicated GPU for best quality right?" → "lets do this experiment is a separate branch" | The GPU-processing experiment (45 % → 12 % of a core) ran on its own branch and was merged only after the owner's visual check (ADR 0005) | DRAFT |
| 16 | 2026-10-15 | "Im not sure if we should name it "color conversion" then the explanation sounds a lot bigger to me. the options should be auto, iGPU/GPU, CPU" | The setting was renamed Processing: Auto / iGPU / GPU / CPU (0.4.2) | DRAFT |
| 17 | 2026-10-15 | "the GPU and CPU feels like the % of CPU andf GPU. make it clearer." | The README's performance tables were restructured so GPU/CPU name where frames are processed, not a usage figure | DRAFT |

To confirm an entry, change its status to **Confirmed** (and correct the wording if it
misrepresents you); delete entries you do not want listed.

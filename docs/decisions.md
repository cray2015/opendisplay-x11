# Decisions

Every decision made on OpenDisplay for X11, with why it was made. New decisions are added
here **when they are made** (CLAUDE.md makes this a rule). A reversed decision is never deleted:
it is marked **Superseded** with a pointer to what replaced it. Details and measurements live in
`PROJECT_SPEC.md` (section in brackets); this file is the short "what and why".

The five most significant decisions also have full records in [`docs/adr/`](adr/README.md); where the owner redirected the AI is in [`docs/human-overrides.md`](human-overrides.md).

Status: **Active**, **Superseded**, **Deferred** (agreed, not built yet), **Open** (asked, not answered).

## Product and scope

| Date | Decision | Why | Status |
|---|---|---|---|
| 2026-10-14 | Validate the spec and prove feasibility (M0 spike) before building | The spec was written without hardware; every risky assumption (transport, forced connector, encoder) had to be shown on this machine first | Active |
| 2026-10-14 | Use the **unmodified OpenDisplay iPad app**; never modify the receiver | Building it needs Xcode on a Mac; the receiver is consumed as shipped (TestFlight / App Store) [§3] | Active |
| 2026-10-14 | **X11 only**; no Wayland | Cinnamon on X11 is the target; Wayland senders already exist upstream [§3] | Active |
| 2026-10-14 | **No terminal for end users, ever** — a Windows-like experience | The user's stated agenda: install by double-click, everything else through a window or the package manager; a step that needs a command is a design bug [§2] | Active |
| 2026-10-14 | **Every tunable lives in the config file**, never as a constant in code | User: "anything configurable should land in a config file"; only protocol- or hardware-fixed values stay in code [§8.6] | Active |
| 2026-10-14 | **No Cinnamon applet**; a desktop-agnostic app instead | User: an applet ties the project to one desktop; it must work on any X11 desktop [§3] | Active |
| 2026-10-14 | **The free video port requirement is stated first** — top of the README and in the package description, short, with a link | Without a spare HDMI/DP++ port the product cannot work at all (X11 cannot create a monitor from nothing); users must learn that before installing. Kept short and jargon-free (KISS) with a link to the README's explanation | Active |
| 2026-10-14 | Name: **OpenDisplay for X11** (package `opendisplay-x11`, command `odx11`), always with "unofficial sender for the OpenDisplay iPad app" | Follows the ecosystem's "OpenDisplay + platform" pattern while making clear it is not upstream's | Active |
| 2026-10-15 | Licence **GPL-3.0-or-later**, © Vibhanshu Biswas, DCO sign-off for contributions | Others can contribute, credit is kept in every copy, changed versions must stay open; matches upstream | Active |
| 2026-10-15 | **One set of settings for every iPad**; only capabilities adapt per iPad (automatically) | Per-iPad preferences add UI for a rare case; decode limits from `hello` are honoured automatically [§8.8] | Active |
| 2026-10-15 | Pencil pressure/tilt, Wi-Fi transport, KMS zero-copy capture, mirror/audio/clipboard | Out of v1 scope; each needs its own milestone | Deferred (M5, M6, v2) |

## Architecture

| Date | Decision | Why | Status |
|---|---|---|---|
| 2026-10-14 | The sender dials the iPad through stock **usbmuxd** (`Connect` to port 9000) | Fixed by the protocol (receiver listens); needs no IP link, custom usbmuxd or adapter [§4] | Active |
| 2026-10-14 | Rejected: browser clients (Weylus, Deskreen) and forking opendisplay-linux | Browser clients need an IP link and manual connection; the fork would replace nearly everything [§4] | Active |
| 2026-10-14 | **Virtual monitor = forced `HDMI-A-n` alias of an empty port**, never the `DP-n` alias | TMDS needs no handshake so HDMI modesets with no sink; DP link training is expected to fail [§4, §8.1] | Active |
| 2026-10-14 | Rejected: EVDI, Intel `VIRTUAL` outputs, DP dummy plug as default | Out-of-tree module with regressions; needs the intel DDX; a dummy plug is always "connected" [§4] | Active |
| 2026-10-14 | Custom EDID via debugfs `edid_override` | — | **Superseded** 2026-10-14: Secure Boot lockdown refuses debugfs writes; run with no EDID [§4] |
| 2026-10-14 | Mode = iPad panel ÷ divisor (1.5), iPad upscales; rotation by `xrandr --rotate` | X11 has one global scale; native Retina at 1× is unreadable; one mode per device [§4, §8.2] | Active |
| 2026-10-14 | The daemon owns monitor placement | — | **Superseded** 2026-10-14 by "Cinnamon owns placement" |
| 2026-10-14 | **Cinnamon owns placement**: add the mode before forcing the connector, never override a restored position | Cinnamon restores its saved layout only if the mode exists at hotplug; config `side`/`align` only for first placement [§4] | Active |
| 2026-10-14 | Python 3 + GStreamer (PyGObject) | `appsink alignment=au` gives one access unit per buffer and force-key-unit answers `kf`; ffmpeg cannot force an IDR from outside [§4] | Active |
| 2026-10-14 | Encoder **`vaapih264enc tune=low-power`, CQP**, never `vah264lpenc`; `keyframe-period` large, never 0 | Measured: `vah264lpenc` answers force-key-unit with a non-IDR I-frame; CBR needs HuC; 0 means an IDR every 30 frames [§4, §8.3] | Active |
| 2026-10-14 | Strict Annex B in our code (`annexb.py`): 4-byte start codes, SPS+PPS before every IDR, one AU per frame | The receiver splits on `00 00 00 01`; encoder output is normalised, never trusted [§4, §7.5] | Active |
| 2026-10-14 | Drop raw frames in a leaky queue, never encoded ones | A dropped P-frame corrupts the picture until the next IDR [§4] | Active |
| 2026-10-14 | `welcome.pv = 2` until Pencil support exists | At pv 3 the receiver sends `pencil` and the stylus goes dead [§4] | Active |
| 2026-10-14 | **Event-driven only, no polling** (usbmuxd Listen, RandR, XDamage, XI2, inotify); timers only for ping, grace, redial, debounce, idle | Earlier Claude-built Cinnamon applets were disabled for idle CPU spikes [§4] | Active |
| 2026-10-14 | Idle gate from **per-window damage** filtered to the virtual monitor, not root damage or `use-damage` | Under Cinnamon's compositor root damage reports the whole screen; `use-damage=true` measured more expensive [§4] | Active |
| 2026-10-14 | `Detached` = intent: tear down at once; grace only for EOF, silence, `sleeping` | Pulling the cable should return windows immediately; app switches should not reshuffle them [§8.4] | Active |
| 2026-10-14 | Protocol framing and demux only in `protocol.py` | Upstream plans protocol v4; the swap must be one file [§4] | Active |

## Privilege and security

| Date | Decision | Why | Status |
|---|---|---|---|
| 2026-10-14 | Root helper via sudoers NOPASSWD | — | **Superseded** 2026-10-14 by the udev rule |
| 2026-10-14 | **udev rule, group `plugdev`, HDMI-A connectors' `status` only — the whole privileged surface; no root code at runtime** | `plugdev` is the established group for pluggable hardware and the installing user is already in it on Debian-family systems; any X client can already switch monitors via xrandr, so the write adds no real capability [§8.7] | Active |
| 2026-10-14 | **No command channel into the daemon** (no socket, D-Bus service or signals) | The window reads a state file and acts through systemd, the config file, `odx11 release`, `idevicepair` — no new attack surface [§8.8] | Active |
| 2026-10-14 | `odx11 release` stays config-independent and only unforces connected-without-EDID connectors | Runs from `ExecStopPost` and `prerm`; must never touch a real monitor [§8.7] | Active |

## Packaging and updates

| Date | Decision | Why | Status |
|---|---|---|---|
| 2026-10-14 | Packaging decided **before** the GUI | The package shape dictates how install, privilege and updates work | Active |
| 2026-10-14 | **.deb first**; rejected Flatpak/Snap, AppImage, pip | The sandbox cannot write sysfs; Snap is off on Mint; pip has no distro deps or privileged part [§4] | Active |
| 2026-10-14 | Service **enabled by default**, started by XDG autostart | Plug in and it works, on any desktop [§8.7] | Active |
| 2026-10-14 | Upgrades restart the running service onto the new version; a brief cut-off is accepted | Updates via any update manager must take effect with no log-out [§8.7] | Active |
| 2026-10-15 | **Restart only when the service itself changed** (fingerprint of daemon modules) | Window-only releases should never blink the iPad [§8.7] | Active |
| 2026-10-15 | Package only **suggests** `intel-media-va-driver-non-free` | It replaces the free driver, so the user chooses; Auto uses it when present [§8.8 GPU] | Active (one-click install button: **Open**) |
| — | Distribution channel (GitHub Releases vs PPA), Software Manager listing | — | Deferred (M9) |
| 2026-10-15 | GitHub Actions: every PR verified, mergeable only with the owner's approval; releases publish the .deb with changelog | Requested; branch protection on a free private repo returned 403 | Built on `trust-setup` (CI + release workflows); branch protection waits for the repo going public |

## Window (GUI)

| Date | Decision | Why | Status |
|---|---|---|---|
| 2026-10-14 | **A normal window, no tray icon** | Identical on every X11 desktop; nothing runs in the panel [§8.8] | Active |
| 2026-10-14 | **GTK 4 + libadwaita** (floor GTK 4.14 / libadwaita 1.5) | GTK 3 is in maintenance mode; Mint patches libadwaita to follow the system theme [§8.8] | Active |
| 2026-10-14 | **Settings apply on their own**, no Apply button | The daemon watches the config and applies each change at the lightest level needed [§8.8] | Active |
| 2026-10-14 | **Notifications as silent as possible** — only when the user must act | User priority: silence; connect/disconnect is visible on the iPad itself [§8.8] | Active |
| 2026-10-14 | Category **Utility** | User's choice | Active |
| 2026-10-14 | Layout review: Start at login under Settings; "Frame rate", "Right-click sensitivity", "Screen timeout"; no settings-file row; remove-monitor button last | User feedback on the first build [§8.8] | Active |
| 2026-10-15 | Show the **version**; no explanation text under it | User asked for version info; the "unofficial sender" note belongs in About, not under Version | Active |

## Performance

| Date | Decision | Why | Status |
|---|---|---|---|
| 2026-10-15 | **Cursor drawn by the iPad** (cursor channel, XFixes) | Faster-feeling cursor; moving the mouse costs almost nothing [§3, M4] | Active |
| 2026-10-15 | **Performance presets**: Power saver, Balanced (default), Best quality, Custom; presets grey out the rows they control | Plain-language choice over the settings that cost CPU, power or bandwidth [§8.8] | Active |
| 2026-10-15 | **Change-driven frames**: convert/encode only for a short tail after a change | btop on the iPad cost ~40 % of a core at 60 fps; 10 % with the gate [§8.8] | Active |
| 2026-10-15 | Ignore damage from minimized windows | A minimized window kept waking capture under the compositor [§8.8] | Active |
| 2026-10-15 | Change tail 50 ms + settle frame (0.3.4) | — | **Superseded** 2026-10-15 (0.3.5): iPad froze on menus; reverted to 150 ms |
| 2026-10-15 | XI 2.2 for raw events (0.3.6) | XI 2.0 clients get no raw events during pointer grabs; the cursor froze while dragging | Active |
| 2026-10-15 | **Keys and clicks over the iPad's monitor wake capture** for `input_tail_ms` (500) (0.3.7) | Cinnamon draws Alt+Tab and its menus with no damage at all | Active |
| 2026-10-15 | **Change tail per preset** (0.3.8): Power saver 100 ms, Balanced 50 ms, Best quality 150 ms | 50 ms retested smooth once the cursor and Alt+Tab bugs were fixed; user: 150 ms for Best quality | Active |
| 2026-10-15 | Benchmark **every preset**; publish tables and a chart in the README | User asked for numbers for all presets | Active |
| 2026-10-15 | GPU experiment on a **separate branch** first | User: keep `main` clean until the experiment is proven | Done (merged 2026-10-15) |
| 2026-10-15 | **Frames processed on the GPU** (`vaapipostproc`) when the driver allows; `[video] convert = auto/gpu/cpu`, auto falls back to the CPU | Measured 45 % → 12 % of a core at 60 fps; slightly better colour accuracy and latency (0.4.0) | Active |
| 2026-10-15 | Window row **"Processing"** with **Auto / iGPU / GPU / CPU** and a one-line explanation (0.4.2) | User: "colour conversion" made the feature sound bigger than it is | Active |
| 2026-10-15 | README: GPU and CPU compared **row by row per preset** ("Frames processed on"), plus a GPU-vs-CPU chart | Column headers "GPU"/"CPU" read as GPU % and CPU % | Active |
| 2026-10-15 | YouTube jitter: **no change to OpenDisplay** | Measured: the desktop itself stalled every 2 s (the privacy-indicator applet's polling and leaked timers); the stream copied the screen faithfully | Active |

## Trust and release

| Date | Decision | Why | Status |
|---|---|---|---|
| 2026-10-15 | Make trustworthiness **verifiable**: CI build, provenance, capability checks, decision records, TRUST.md; badges only for checks that run green | The repo is built with heavy AI assistance; outsiders need evidence, not claims | Active |
| 2026-10-15 | **The repository becomes public** (after a full-history secret scan came back clean) | Free provenance attestations, CodeQL, Scorecard and private vulnerability reporting need a public repo | Active |
| 2026-10-15 | **Publish the .deb on GitHub Releases** from version tags, with SHA256, SBOM and a provenance attestation | Gives users one verifiable download; completes M10's release half | Active |
| 2026-10-15 | **Security contact: the owner, via GitHub private vulnerability reporting** | No email address published; reports stay private until fixed | Active |
| 2026-10-15 | Capability changes checked by **Semgrep with a project rules file** (`--baseline-commit`), not a third-party capability scanner | No mature Python capability scanner exists (searched GitHub, PyPI, npm, crates.io, web); the rules can name this project's real capabilities (XTest, XInput2, sysfs, D-Bus) | Active |
| 2026-10-15 | Pin actions and tool images to versions **at least 7 days old**; Dependabot with a 7-day cooldown | A brand-new release is the most likely to be compromised or broken | Active |
| 2026-10-15 | Coverage floor **50 %** (measured 52 %), **raised to 65 %** (measured 69 %) after the fake-based tests; mutation testing reported monthly, not a gate | Prevents decline without failing on the untestable hardware modules; the mutation baseline needs tests added before it can gate | Active |
| 2026-10-15 | **`edid.py` excluded from mutation testing** (its unit tests stay) | It only serves the boot-time EDID fallback, which is not used at runtime (§4: no EDID on this machine); revisit if a named monitor is ever built | Active |
| 2026-10-15 | **X11 tests run against Xorg's dummy driver in a container** (`tests/x11/`), the same image locally and in CI; they skip unless `ODX11_X11_TESTS=1` | Xvfb cannot add RandR modes, which the virtual monitor needs; the dummy driver can. The guard keeps tests that move the pointer and click off a desktop someone is using | Active |
| 2026-10-15 | `pipeline.py` starts GStreamer in an explicit `init()` at service start, not on import | Importing it ran `gst-inspect` and `Gst.init`, so its logic could not be tested; `init()` keeps the old timing (before the port is forced), so behaviour is unchanged | Active |
| 2026-10-15 | Coverage floor **75 %** (measured 77 % with the X11 tests), **then 78 %** (measured 82 % with the pipeline tests); measured in the X11 job | Each step of real tests raised the floor so the gain cannot slip back | Active |
| 2026-10-15 | CI runs on **pushes to main, pull requests and by hand**, not on every branch push | A branch's pushes are checked through its pull request; running both doubled every run | Active |
| 2026-10-15 | **Monthly mutation testing runs in the X11 image** (4 workers, memory capped); locally only capped (`--max-children 2`, `docker --memory=2g --cpus=2`) | The input injector's mutants need an X server; an uncapped local run filled the desktop's RAM | Active |
| 2026-10-15 | Releases also publish the **.buildinfo** | Records the exact toolchain so independent rebuilds can match the hash | Active |
| 2026-10-15 | README badges only for checks that have run green on GitHub | A badge without evidence is the problem the trust setup solves | Active |
| 2026-10-15 | No pip-audit / osv-scanner | No PyPI dependencies or lockfile: they would scan nothing; Ubuntu packages' CVEs are tracked by Ubuntu | Active |

## Process

| Date | Decision | Why | Status |
|---|---|---|---|
| 2026-10-14 | Commit and push **only when the user asks**; commits signed off (`-s`) | User controls history; DCO | Active |
| 2026-10-15 | **Changes to frame gating, tails, idle pausing or frame rate need a hands-on check on the iPad** before they count as a win | The CPU benchmark cannot see freezes (0.3.4) | Active |
| 2026-10-15 | Keep a decisions log (this file) and a mistakes log (CLAUDE.md) | User request | Active |

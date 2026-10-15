# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## Build Commands
```bash
# deps — system packages (Mint 22.3 / Ubuntu 24.04)
sudo apt install usbmuxd libimobiledevice-utils \
  python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-vaapi python3-xlib python3-pytest vainfo intel-media-va-driver
# Expands to: usbmuxd + idevice tools, PyGObject + GStreamer introspection,
# ximagesrc (good), h264parse (bad), vaapih264enc (vaapi), XTest bindings,
# pytest, iHD VA-API driver (free build — what M0 was verified on)

# install — developer path: checks the machine, builds dist/*.deb, installs it (password dialog)
bash install.sh --check && bash install.sh
bash install.sh --build                      # only build the .deb (unit tests run inside the build)

# venv — only if one is needed; PyGObject comes from apt
python3 -m venv --system-site-packages .venv

# run — foreground session against the attached iPad
python3 -m odx11 run --verbose

# list — attached iOS devices as usbmuxd reports them
python3 -m odx11 list

# service — installed by the package; logs; restart after changing code or config
journalctl --user -u odx11 -f
systemctl --user restart odx11

# test — unit tests; need no iPad, no X server, no GPU
python3 -m pytest -q tests/

# fake receiver — exercise framing and the session without an iPad
python3 tools/fake_receiver.py --port 9000

# pipeline check — the encoder M0 verified (vah264enc/vapostproc do not exist here)
gst-inspect-1.0 vaapih264enc

# M0 spikes — throwaway; connector test needs sudo, probe needs the app foreground
bash spike/m0_connector.sh HDMI-A-1
python3 spike/hello_probe.py --pattern

# diagram — render on demand, never commit the SVG
d2 docs/architecture.d2 docs/architecture.svg
```
The venv must use `--system-site-packages`: PyGObject and the GStreamer
typelibs are apt packages and cannot be pip-installed cleanly here.
Tests need no iPad, X server or GPU (and no python-xlib, GStreamer or GTK): pure
modules directly; usbmuxd, D-Bus, subprocesses and the daemon's GLib main loop
through fakes (`tests/test_usbmux.py`, `test_daemon_loop.py`, …). Real X11,
GStreamer, the window and the hardware are verified by hand against the acceptance
criteria in `PROJECT_SPEC.md` §10. Mutation testing (mutmut, config in
`pyproject.toml`): after changing a module's tests run `mutmut run "odx11.<module>*"`;
the pure modules are at 100 %, mark only provably equivalent mutants
`# pragma: no mutate` with the reason.

## Layout
- `odx11/protocol.py` is the only module that knows the wire format (framing, demux, message shapes).
- `odx11/annexb.py` owns every NAL-level rule; `pipeline.py` never edits bytes.
- `odx11/display.py` is the only caller of xrandr and the only writer of the connector's sysfs `status`.
- `odx11/daemon.py` owns every timer and the session state machine.
- `odx11/gui.py` is the settings window (GTK 4 + libadwaita, own process); it only reads `state.json` and acts via systemd, the config file, `odx11 release`, `idevicepair`.
- `odx11/state.py` `describe()` is the only place status wording lives (window and notifications share it).
- Nothing runs as root. `packaging/70-opendisplay-x11.rules` (udev) is the project's entire privileged surface; `debian/` builds the `.deb`.

## Key Constraints
- **Emit only 4-byte start codes and prefix every IDR with SPS and PPS** — the receiver splits on `00 00 00 01`; a 3-byte code or a bare IDR decodes as garbage or black. See `PROJECT_SPEC.md` §7.5.
- **Send exactly one access unit per wire frame** — never forward raw encoder chunks. See `PROJECT_SPEC.md` §7.5.
- **Send usbmuxd `PortNumber` in network byte order (htons(9000) = 10275)** — a host-order 9000 dials the wrong port and is refused. See `PROJECT_SPEC.md` §7.1.
- **Never emit a control message ≥ 32768 bytes, not starting with `{`, or containing a NUL byte** — the receiver's demux would treat it as video. See `PROJECT_SPEC.md` §7.2.
- **Keep framing and demux inside `protocol.py`** — protocol v4 replaces the demux heuristic and the swap must be one file. See `PROJECT_SPEC.md` §4.
- **Advertise `welcome.pv = 2` until Pencil handling exists** — at pv 3 the receiver sends `pencil` instead of degrading to `touch`, and the stylus goes dead. See `PROJECT_SPEC.md` §4.
- **Reply to every `hello` with `welcome`, and to every `ping` with `pong` echoing `t` unchanged** — receivers rely on both. See `PROJECT_SPEC.md` §7.4.
- **Send a `ping` at least every 2 s, even on a static screen** — the receiver drops the connection after 5 s of silence. See `PROJECT_SPEC.md` §7.3.
- **Treat `touch.x/y` as normalised 0..1 and `scroll.dx/dy` as video pixels with natural sign** — mixing the units is the classic third-party bug. See `PROJECT_SPEC.md` §7.6.
- **Never press a button on touch `began`; decide on move/lift, and let a still `cancelled` touch evaporate** — a two-finger scroll arrives as `began` + `cancelled`, and an X11 press cannot be retracted, so an early press clicks under finger one. See `PROJECT_SPEC.md` §7.6.
- **Map input with the output geometry re-queried after every xrandr change** — stale geometry sends clicks to the main monitor. See `PROJECT_SPEC.md` §7.6.
- **Drop raw frames in the leaky queue, never encoded frames; if an encoded frame is skipped, force an IDR next** — a missing P-frame corrupts the picture until the next IDR. See `PROJECT_SPEC.md` §4.
- **Encode with `vaapih264enc tune=low-power rate-control=cqp`, never `vah264lpenc`** — on this HD 530 `vah264lpenc` answers force-key-unit with a non-IDR I-frame, so `kf` never recovers the receiver. See `PROJECT_SPEC.md` §4.
- **Set `keyframe-period` to a large value, never 0** — 0 means auto (an IDR every 30 frames), not off. See `PROJECT_SPEC.md` §8.3.
- **Size the display from `hello.panel`, falling back to `pixelsWide/High` only when `panel` is absent or malformed** — the old fields are deprecated upstream. See `PROJECT_SPEC.md` §8.2.
- **Force the `HDMI-A-n` alias, never the `DP-n` alias** — DP link training fails with no sink attached. See `PROJECT_SPEC.md` §8.1.
- **Add the mode before forcing the connector, and never override a position Cinnamon restored** — Cinnamon owns placement and only restores its saved layout if the mode already exists at hotplug. See `PROJECT_SPEC.md` §4, §8.1.
- **On teardown, `detect` before `--delmode`/`--rmmode`** — while still forced, Cinnamon re-enables the output and the mode cannot be removed. See `PROJECT_SPEC.md` §8.1.
- **Unforce the connector on every exit path (atexit + `ExecStopPost`)** — a connector left forced on is an invisible monitor that swallows windows. See `PROJECT_SPEC.md` §8.4.
- **Never add root code; the udev rule (group `plugdev`, HDMI-A connectors' `status` only) is the whole privileged surface** — end users install a `.deb` and never use a terminal; DP connectors stay excluded. See `PROJECT_SPEC.md` §8.7.
- **`odx11 release` must stay config-independent and only unforce connectors that are connected with no EDID** — it runs from `ExecStopPost` and the package's `prerm`, and must never touch a real monitor. See `PROJECT_SPEC.md` §8.7.
- **Never poll; use usbmuxd `Listen`, RandR, per-window XDamage, XI2 and inotify (config, state, VERSION) events, with timers only for ping, grace, redial, rotation debounce, the idle pause and upgrade settling** — earlier Cinnamon applets were disabled for idle CPU spikes. See `PROJECT_SPEC.md` §4.
- **Never gate capture on root-window damage** — Cinnamon's compositor reports every repaint as the whole screen; use per-window damage filtered to the virtual monitor (`odx11/activity.py`). See `PROJECT_SPEC.md` §4.
- **Treat `Detached` as intent: tear down immediately, no grace** — grace is only for EOF, silence and `sleeping`. See `PROJECT_SPEC.md` §8.4.
- **Every tunable lives in the config file (`odx11/config.py` + `config.example.toml`), never as a module constant** — only values the protocol or hardware fixes stay in code; `tests/test_config.py` keeps the example equal to the defaults. See `PROJECT_SPEC.md` §8.6.
- **Never add a command channel into the daemon (socket, D-Bus service, signal protocol)** — the window reads the state file and acts through systemd and the config file; the daemon applies config changes by watching the file. See `PROJECT_SPEC.md` §8.8.
- **Wrap every user-visible string in `_()` (plurals: `ngettext`) and use `.format()` placeholders, never f-strings, inside them** — logs and the diagnostic report stay English. See `PROJECT_SPEC.md` §8.8.
- **Keep the desktop entry, icon and AppStream ID equal to the app ID and validated** — the build runs `desktop-file-validate` and `appstreamcli validate`; lintian must stay clean. See `PROJECT_SPEC.md` §8.8.
- **Keep `upgrade.GUI_ONLY` to modules the daemon never imports** — window-only upgrades skip the service restart by fingerprint; the AST test enforces it. See `PROJECT_SPEC.md` §8.7.
- **Any new capability (running a program, a socket, a file write, a device path, D-Bus, input, screen, clipboard, dynamic code) is added to `TRUST.md` and, if the rules miss it, to `.semgrep/capabilities.yml` in the same change** — CI fails a PR that adds one unreviewed; TRUST.md is the public promise. See `TRUST.md`.
- **Pin every GitHub Action to a full commit SHA of a release at least 7 days old, give each workflow no or read-only top-level permissions, and keep zizmor (pedantic) clean** — see `.github/workflows/`.
- **Ignore unknown control `type`s and fields, logging each unknown type once** — input arrives at hundreds of messages per second. See `PROJECT_SPEC.md` §7.4.

## Working Rules
- **Record every decision in `docs/decisions.md` in the same change that acts on it** — date, what, why, status; mark reversed ones *Superseded* with a pointer, never delete them. A decision the user makes in conversation counts, even with no code change.
- **Log every mistake in "Mistakes" below as soon as it is recognised** — whether the user points it out or you notice it: what happened, its cost, and the rule that prevents it. Read this list before starting work.

## Mistakes
Each entry is a mistake Claude made on this project and the rule it leaves behind.

1. **Stated the hard constraint too late** (2026-10-14) — the user found the "spare HDMI port required" limit in the .deb description, not up front. *Rule: a constraint that makes the product useless for some users goes first in the README and package description.*
2. **Package description too long and technical** (2026-10-14) — the user had to ask for KISS and a link. *Rule: user-facing text is short, plain words, with a link for detail.*
3. **Left the user unsure whether the GUI existed** (2026-10-14) — they installed the .deb and found no window. *Rule: when handing over a build, say what is and isn't in it yet.*
4. **Defaulted to a maintenance-mode toolkit** (GTK 3) (2026-10-14) — the user pushed back. *Rule: prefer the actively developed option, and say so when recommending.*
5. **Over-claimed `plugdev` membership** (2026-10-14) — said desktop users are in it; only the installer-created account is. *Rule: verify a system fact before stating it as general.*
6. **Put an explanation where none belonged** (2026-10-15) — the "unofficial sender" note under the Version row; the version itself had been missing. *Rule: one fact per row; check the window shows basics like the version.*
7. **Shipped a 50 ms change tail as a win from CPU numbers alone** (0.3.4, 2026-10-15) — the iPad froze on menus; reverted in 0.3.5. *Rule: gating/tail/fps/idle changes need a hands-on iPad check before being called an improvement.*
8. **Built the cursor channel without testing pointer grabs** (0.3.0) — the cursor froze while dragging or with a menu open (XI 2.0); fixed in 0.3.6. *Rule: test input features during drags, menus and other grabs.*
9. **Misdiagnosed the Alt+Tab lag as the tail length** (2026-10-15) — reverting to 150 ms changed nothing; the switcher has no damage at all. *Rule: first test with every frame sent (`change_tail_ms = 0`) to separate gating from other causes, and record the screen beside the wake log.*
10. **Charted two series with Mermaid** (2026-10-15) — GitHub stacked them into one confusing bar. *Rule: draw multi-series charts as generated SVG and look at the render before showing it.*
11. **Named a folder ambiguously in a status update** (2026-10-15) — "airdrop_support" made the user think AirDrop had to be turned off. *Rule: say what a name is (a window title, a folder) when it could read as an instruction.*
12. **Said "side by side" without making the chart** (2026-10-15) — the user looked for a new chart that did not exist. *Rule: when a request implies a visual, make it, or say plainly that none was made.*
13. **Labelled table columns "GPU"/"CPU" over CPU percentages** (2026-10-15) — read as GPU % and CPU %. *Rule: a header names what is measured; a setting's value goes in its own labelled column.*
14. **Leaked a duplicate applet timer while testing** (2026-10-15) — toggling the privacy-indicator applet's settings and enabling it again left two poll loops until Cinnamon was restarted. *Rule: before changing another program's state to test something, know how to fully restore it, and verify the restore (not only the config file).*
15. **Placed a test window on a monitor that wasn't there** (2026-10-15) — during the YouTube-jitter check, the "player alone on the iPad's monitor" run happened with the service stopped, so that monitor did not exist and the window landed on the main one. *Rule: check the setup (monitors, service state) before each measurement.*
16. **Claimed "no capability scanner exists for Python" from one GitHub search** (2026-10-15) — a second look found CapabilityEcho (Python-capable PR-diff tool), an `overreach` crate (repo since deleted) and others; the conclusion (none mature or fitting) held, the claim as stated did not. *Rule: before saying a tool does not exist, search the registries (PyPI, npm, crates.io) and the web, not one site, and state what was searched.*
17. **Invented the times in `debian/changelog`** (0.1.0–0.4.2) — placeholder times running ahead of the clock put the newest entry in the future, so dpkg did not clamp file times and builds were not reproducible (found 2026-10-15). *Rule: changelog dates come from `date -R` at the moment of the release commit, never made up.*
18. **Used Python 3.12-only syntax in a package that declares Python ≥ 3.11** (0.3.8, `pipeline.py`: same quotes inside an f-string's `{}`) — a SyntaxError on 3.11; unnoticed because this machine runs 3.12 (found by ruff 2026-10-15). *Rule: ruff's `target-version` matches the lowest supported Python; CI runs it.*

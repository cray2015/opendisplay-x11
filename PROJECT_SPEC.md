# OpenDisplay for X11 (opendisplay-x11) — project spec

**Licence (decided 2026-10-15): GPL-3.0-or-later**, © 2026 Vibhanshu Biswas — others can contribute (DCO sign-off, `CONTRIBUTING.md`), credit is kept in every copy, and changed versions must stay open; matches upstream OpenDisplay and the Linux Wayland fork (both GPL-3.0). No code was copied from them (the protocol was implemented from its published description). Every source file carries REUSE/SPDX headers; the AppStream file is CC0-1.0.

**Name (decided 2026-10-14):** users see **OpenDisplay for X11**, always next to "unofficial sender for the OpenDisplay iPad app" — it follows the ecosystem's "OpenDisplay + platform" pattern (`OpenDisplay Linux Fork`, `opendisplay-android`) while making clear it is not part of upstream, which states it makes no commitment to third-party implementations. Technical identifiers stay `opendisplay-x11` (package, repo) and `odx11` (command, Python package).

## 1. Overview
A Linux X11 sender for the OpenDisplay protocol that turns an iPad into a wired,
extended second monitor for the HP EliteDesk 800 G3 DM (i5-6500T, Intel HD 530)
running Linux Mint 22.3 Cinnamon on X11. The unmodified OpenDisplay iPad app
listens on TCP 9000; this daemon dials it through stock `usbmuxd` over the USB
cable, creates a virtual monitor by forcing a spare display connector on,
captures it, encodes H.264 on the iGPU via VA-API, and injects touch back into
X. Target experience is spacedesk-over-USB: plug in, open the app, the desktop
extends. Current phase: **M2 — seamless daemon** (M0 spike and M1 MVP complete).
M1 result (2026-10-14): `odx11 run` extends the desktop to the iPad over USB;
tap, drag, long-press right-click and two-finger scroll work; the session
follows the virtual monitor when it is moved in Cinnamon; Ctrl-C tears down.
M0 result (2026-10-14): feasible. Transport, handshake, VA-API encode, a
forced connector at the §8.2 mode, live capture of the virtual monitor shown
on the iPad, and windows returning to the main monitor on unforce are all
proven. Carried forward: whether debugfs `edid_override` is honoured (needed
for M2's named monitor and persistent placement). Throwaway spike scripts live in `spike/`.

## 2. Goals
- **No terminal, ever, for an end user** — a Windows-like experience: install by double-clicking a package and entering the password in the desktop's standard dialog; setup, status, settings, troubleshooting and uninstall all through the GUI or the package manager. Any step that needs a command is a bug in the design. (`install.sh` and the CLI remain for developers only.)
- Plugging in the iPad and opening the OpenDisplay app extends the Cinnamon desktop onto it with no command run.
- The iPad appears in Cinnamon's Display settings as a monitor, and its placement persists across reconnects and reboots (Cinnamon remembers it; a *named* monitor needs the boot-time EDID, skipped — §11).
- Pulling the cable or quitting the app returns every window to the main monitor; a brief drop (app switch, lock) does not reshuffle windows.
- Tap clicks, drag drags, and two-finger scroll scrolls, all landing on the iPad monitor's area.
- An idle desktop costs near-zero CPU; active use stays inside a measured budget on the i5-6500T.
- Works over any USB 2.0 data cable with stock Mint packages — no custom usbmuxd build, no USB networking, no Ethernet adapter, no browser.
- **Hard constraint, stated first everywhere a user meets the product** (README top, package description): a free video port is required. X11 with the `modesetting` driver offers outputs only for kernel connectors, so an app cannot create a monitor out of nothing (Windows has the Indirect Display Driver framework, macOS `CGVirtualDisplay`, Wayland compositors their own virtual outputs; X11 has no equivalent). Works: a free HDMI socket, or a free DP++ socket (it has an `HDMI-A-n` alias — the development machine). Untested: USB-C/Thunderbolt-only and AMD DP-only machines (forcing a `DP-n` connector has never actually been tried — §4's 'fails link training' is an assumption). No free port: dummy plug, or rejected options (EVDI, vkms, intel DDX `VirtualHeads`, an `--fb`/`--setmonitor` software region) — README 'Why a free video port is required'.
- Desktop-agnostic on X11 (any desktop or window manager): nothing may depend on Cinnamon-only APIs; desktop features (e.g. remembered placement) are used when present and fallen back from when not. Hardware stays the real constraint: a kernel DRM GPU with a spare HDMI/DP++ port. User-facing limitations are listed in README.md.

## 3. Non-Goals
<!-- Tag names when the item could come back, not what release excluded it:
     [never]       — permanently out of scope, architectural or by principle
     [v2] / [v3]   — deferred to that release
     [unplanned]   — not ruled out, not scheduled
     [superseded]  — reversed; annotate in place, never delete -->
- **[never]** Modifying or rebuilding the iPad receiver app — building it needs Xcode on a Mac; the receiver is consumed as shipped (TestFlight / App Store).
- **[never]** Wayland session support — Cinnamon on X11 is the target; Wayland senders already exist upstream (tixwho/opendisplay-linux for KDE and Hyprland).
- **[never]** Game-streaming latency — tuned for desktop work; Sunshine/Moonlight is the tool for games and is explicitly out of the brief.
- **[v2]** Apple Pencil pressure/tilt — needs `pencil`/`proximity` handling, a uinput tablet device, and `welcome.pv` raised to 3 (M5).
- **[superseded 2026-10-15]** Separate cursor channel (`cursor` + `cursorImg` via XFixes) — v1 bakes the cursor into the video (M4). *Built in 0.3.0 at the user's request for a faster-feeling cursor.*
- **[v2]** Wi-Fi transport via Avahi `_opensidecar._tcp` — USB first (M6).
- **[v2]** KMS zero-copy capture — only if XShm capture misses the CPU budget in M3.
- **[superseded 2026-10-14]** Cinnamon panel applet for status/toggle — only if event-driven (see §4, CPU constraint). *Rejected by the user: an applet ties the project to one desktop.*
- **[unplanned]** A desktop-agnostic status/toggle app (any X11 desktop or window manager) — must stay event-driven and must not make the daemon depend on it; any daemon↔UI channel is a new local interface and needs the same scrutiny as the root helper.
- **[unplanned]** Mirror mode, audio forwarding, clipboard sync, multiple iPads at once.
- **[superseded 2026-10-14]** .deb packaging was listed here as unplanned. *Now the installation target: see §4 Packaging.*

## 4. Design rationale and constraints
- **The receiver listens, the sender dials** — fixed by the protocol (§7.1). This is why plain `usbmuxd` is enough: the sender asks usbmuxd to `Connect` to device port 9000 and gets a raw byte pipe. No IP link exists or is needed over the cable.
- **Rejected: browser clients (Weylus, Deskreen)** — they need an IP link over the cable (custom NCM-enabled usbmuxd build or a USB-Ethernet adapter on the iPad), decode in Safari, and connect manually. Not spacedesk-like.
- **Rejected: forking tixwho/opendisplay-linux (C++/Qt)** — an X11 backend replaces its capture (PipeWire portal), display control (KScreen/Hyprland), input and Kirigami GUI. What would survive (usbmux connect, framing) is the small part. Keep it as a protocol reference only.
- **Virtual monitor = forced HDMI alias of an empty DP++ port** — i915 exposes each DP++ port as both `DP-n` and `HDMI-n`. TMDS needs no handshake, so a forced HDMI connector modesets with no sink attached; forcing the `DP-n` alias is expected to fail link training. This exact sysfs trick is known to work for an iPad on Mint X11 (hkfuertes/virtual-screen).
- **Rejected virtual-display alternatives** — EVDI (out-of-tree module, reported black-screen regressions after updates); Intel `VIRTUAL` outputs (need xf86-video-intel, Mint runs the modesetting driver); hardware DP dummy plug (monitor is always "connected", so Cinnamon enables an invisible screen when no iPad is present — fallback only).
- **[superseded 2026-10-14] Custom EDID on the forced connector** — gives Cinnamon a stable, named monitor identity and the exact modes we want. Preferred delivery is runtime via debugfs `edid_override` (per-device size, no reboot); boot-time `drm.edid_firmware=` is the fallback (§11). *M2 finding: Secure Boot puts the kernel in lockdown (`integrity`), which refuses debugfs writes even from root, so the runtime override cannot work on this machine. Decision: run without an EDID — the monitor shows as `unknown` in Display settings, everything else works, and the mode stays free to vary per iPad and on rotation. `odx11/edid.py` + `edid/make_edid.py` remain for the boot-time fallback if a named monitor is ever wanted.*
- **Lower logical resolution than the iPad panel** — X11 has one global scale and Cinnamon's per-monitor fractional scaling on X11 renders via `xrandr --scale`, which costs GPU. Driving the panel at native Retina size at 1x makes text unreadably small. The virtual mode is panel ÷ divisor (default 1.5) and the iPad upscales (§8.2).
- **Rotation via `xrandr --rotate`, not new modes** — the EDID/mode stays landscape; portrait is `--rotate left`. Keeps one mode per device.
- **Strict Annex B in our own code** — the receiver requires 4-byte start codes, SPS+PPS before every IDR, and one access unit per wire frame (§7.5). 3-byte start codes are legal H.264 byte-stream, so encoder output is normalised in `annexb.py`, never trusted.
- **Drop raw frames, never encoded ones** — backpressure is a leaky queue before the encoder. Dropping an encoded P-frame breaks the reference chain and shows corruption until the next IDR; if an encoded frame must be skipped, the sender forces an IDR next.
- **`welcome.pv = 2` until Pencil lands** — a receiver told the sender is pv 3 sends `pencil` messages instead of degrading the stylus to `touch`. Advertising 3 before M5 makes the Pencil go dead.
- **Python 3 + GStreamer (PyGObject) for v1** — `appsink` with `alignment=au` yields one access unit per buffer, and an upstream force-key-unit event (sent with `appsink.send_event`) answers `kf`. Rejected: ffmpeg subprocess (cannot force an IDR on demand from outside the process); Go (cgo bindings for GStreamer or libav add build friction before the design is proven).
- **Encoder is `vaapih264enc tune=low-power`, CQP — measured in M0** — with Mint's free `intel-media-va-driver` (iHD) the HD 530 exposes only `VAEntrypointEncSliceLP`, so `vah264enc` and `vapostproc` do not exist and low-power rate control is CQP only (CBR needs HuC). `vah264lpenc` answers force-key-unit with a non-IDR I-frame (and ignores repeats), which breaks `kf`; `vaapih264enc` emits a real IDR with SPS+PPS the next frame. The i965 driver's encoder aborts at context init. CQP is acceptable: USB 2.0 has headroom for scene-change bursts. Colour conversion is software `videoconvert` (no VPP entrypoint). Fallback: `x264enc` ultrafast/zerolatency (~31 % of one core vs ~19 %). Risk: gstreamer-vaapi is deprecated upstream; re-check when Mint moves past GStreamer 1.24.
- **Idle cost needs a damage gate, not `use-damage`** — `ximagesrc` emits at the full frame rate regardless, and `use-damage=true` measured *more* expensive (35 % vs 19 % of one core) because it copies per damaged rectangle. Near-zero idle requires the daemon to watch XDamage on the virtual output and stop pushing frames when nothing changes, resuming on damage; while paused, `kf` and reconnects replay the last frame as an IDR (upstream §5.3).
- **Idle gate as built (M3)** — `odx11/activity.py`. Root-window DAMAGE is useless under Cinnamon's compositor: every repaint reports the whole screen, so one busy window anywhere (this terminal, `btop`) keeps it firing ~15/s. Instead each top-level window gets its own DAMAGE object (`NonEmpty`, re-armed by subtract), SubstructureNotify keeps window geometry current, and a damage/map/unmap/move wakes capture only if the window overlaps the virtual monitor; XI2 raw motion wakes while the pointer is over it (or just left — the cursor is baked in and moves without repaints). The session pauses the pipeline (`PAUSED`: no capture, convert or encode) after `idle_pause_s` without a wake; `kf`, touch and a new pipeline wake it. Measured on the iPad: **1.0 % of one core idle vs 20 % ungated**; ~20 % while the iPad monitor changes. Not seen: pixels the compositor draws itself (panels, OSDs, notifications) — primary-monitor only by default. Python-xlib note: a reply wait can pull the first DamageNotify into Xlib's queue, after which the socket never turns readable and `NonEmpty` never re-fires — drain once after setup.
- **No polling — event-driven only** — device events come from usbmuxd `Listen`, display changes from RandR events. The only timers: 2 s ping, 10 s grace, 3 s redial while a device is attached with no session. Previous Claude-Code-built Cinnamon applets were disabled for CPU spikes; this project must be quiet at idle.
- **[superseded 2026-10-14 by the udev privilege below]** **Root only for the connector toggle** — one allowlisted helper script via sudoers NOPASSWD (§8.5). The daemon runs as the desktop user under `systemd --user` with the session's `DISPLAY`/`XAUTHORITY`.
- **Packaging: .deb first (decided 2026-10-14)** — installation is built around a Debian package for Mint/Ubuntu/Debian: dependencies declared (GStreamer/VA-API/PyGObject/python-xlib/usbmuxd come from the distro; pip cannot provide them), FHS paths (no repo-relative `PYTHONPATH`), double-click install with no terminal. RPM/Arch can follow on the same layout. Rejected: Flatpak/Snap (the sandbox cannot write the connector's sysfs status; Snap is off on Mint), AppImage and pip/pipx (no distro deps, no privileged part). `install.sh` stays as the developer path from a git checkout. Distribution channel (GitHub Releases vs PPA) is deferred.
- **Privilege: udev group rule replacing the root helper (verified, built in M7)** — a udev rule makes HDMI connectors' `status` attribute writable by `plugdev` (the backlight-brightness pattern), so the daemon writes it directly and **no root code runs at runtime**. Verified on this machine before building: a non-root writer with group permission forced the connector. Details §8.7.
- **[superseded 2026-10-14] The daemon owns placement** — position is applied by the daemon from per-device config on every attach, rather than relying on Cinnamon's `monitors.xml`, so layout is deterministic. M0 showed Cinnamon enables and places a hotplugged output on its own, before our xrandr runs; the daemon must apply its layout after that settles (§11).
- **Cinnamon owns placement** (replaces the above) — Cinnamon saves the arrangement in `~/.config/cinnamon-monitors.xml`, keyed by connector (`HDMI-1`, vendor/product/serial `unknown` without an EDID), and restores it on hotplug — *provided our mode already exists on the output*. So the daemon adds the mode **before** forcing the connector, waits up to `settle_timeout_s` for Cinnamon to enable it, and only fixes what is left: mode/rotation in place if the orientation differs, or a first-time position from `[devices]` `side`/`align` when Cinnamon has no saved layout. Measured: attach ≈ 1 s when Cinnamon restores. Limitation: all iPads share one identity, so two iPads cannot have different placements (multiple iPads are a non-goal).
- **Protocol drift** — upstream plans protocol v4 to replace the JSON/video demux heuristic with a typed frame header. Framing and demux live in one module so the swap is one file.
- **External dependency: receiver distribution** — the iPad app ships via TestFlight; App Store release is on upstream's roadmap, not done. If the beta closes before an App Store release, the project is blocked.

## 5. Architecture
usbmuxd `Attached` → `daemon.py` dials device port 9000 via `usbmux.py` → receiver sends `hello` → `display.py` forces the HDMI alias on (writes its sysfs `status`; udev rule, no root), sets mode, rotation and placement with xrandr → `pipeline.py` captures that output's region with `ximagesrc` and encodes VA-API H.264 → `annexb.py` normalises each access unit → `protocol.py` frames it onto the usbmux socket → iPad decodes and displays. Back-channel: `touch`/`scroll` → `input.py` → XTest at the output's geometry; `kf` → force IDR; `ping` → `pong`.
Diagram source: `docs/architecture.d2`. Render on demand with
`d2 docs/architecture.d2 docs/architecture.svg` — the `.d2` is the source of
truth; the rendered SVG is not committed.

## 6. Components
| Path | Role |
|---|---|
| `odx11/__main__.py` | CLI entry: `run` (foreground session), `daemon` (service mode), `list` (attached devices) |
| `odx11/daemon.py` | `Session` (one connection), `Daemon` (§8.4 state machine: attach/detach, dial, grace, redial, teardown), GLib wiring incl. the usbmuxd socket watcher; owns all timers |
| `odx11/edid.py` | EDID 1.4 builder — kept for the boot-time fallback; unused at runtime (§4) |
| `odx11/usbmux.py` | Minimal usbmuxd client over `/var/run/usbmuxd`: `Listen` (incremental reader), `Connect(device, 9000)`, `ListDevices` |
| `odx11/protocol.py` | Length-prefix framing, demux rule, control-message encode/decode — the only module that knows the wire format |
| `odx11/annexb.py` | NAL parsing, 3→4-byte start-code normalisation, SPS/PPS cache and IDR prefixing, AUD stripping |
| `odx11/pipeline.py` | GStreamer pipeline build/rebuild, appsink pull, force-key-unit, leaky-queue backpressure |
| `odx11/display.py` | Only caller of xrandr and only writer of the connector's sysfs `status` (`on`/`detect`, `release_forced`); mode sizing (§8.2), rotation, placement, geometry query |
| `odx11/gui.py` | The settings window (GTK 4 + libadwaita), its own process: status, on/off, settings applied live, setup checklist, help (§8.8) |
| `odx11/state.py` | State file write/read and `describe()` — the one source of user-facing status wording |
| `odx11/notify.py` | Desktop notifications over D-Bus (outgoing only); `decide()` picks which a transition earns |
| `odx11/settings.py` | Window controls ↔ config values; canonical TOML writing (non-defaults only) |
| `odx11/checks.py` | The setup checklist and pairing |
| `odx11/i18n.py` | gettext: every user-visible string goes through `_()` / `ngettext()` |
| `odx11/activity.py` | Idle-gate wake signals: per-window DAMAGE + XI2 raw motion, filtered to the virtual monitor (§4) |
| `odx11/input.py` | `touch`/`scroll` → XTest motion, button 1, wheel buttons 4–7, within current output geometry |
| `odx11/config.py` | TOML config load and validation; every tunable (§8.6) |
| `packaging/70-opendisplay-x11.rules` | udev rule: HDMI-A connectors' `status` writable by `plugdev` — the whole privileged surface |
| `packaging/opendisplay-x11.desktop` | XDG autostart entry → `odx11 autostart` |
| `debian/` | Package definition: control, rules (tests run in the build), maintainer scripts |
| `edid/make_edid.py` | Generates a landscape EDID blob for a given logical mode |
| `packaging/io.github.cray2015.OpenDisplayX11.desktop`, `.svg`, `.metainfo.xml` | App menu entry, hicolor icon, AppStream metadata — validated by `desktop-file-validate` / `appstreamcli` in the build |
| `packaging/odx11.1` | Man page for `/usr/bin/odx11` (Debian policy) |
| `packaging/odx11.service` | User unit; `ExecStopPost=odx11 release` always unforces; `Restart=on-failure` except on exit 78 (configuration) |
| `install.sh` | Developer path: machine checks, builds the `.deb`, installs it via the package manager |
| `tools/fake_receiver.py` | Local receiver for tests without an iPad: sends `hello`, pings, counts AUs, validates framing, telemetry and `kf` → IDR |
| `tools/idle_cpu.sh` | AC 23: the service's CPU over an idle period |
| `tests/` | Unit tests for protocol, annexb, mode sizing, coordinate mapping, state machine transitions |
| `docs/architecture.d2` | Architecture diagram source |

## 7. Protocol (OpenDisplay, sender side)
Normative source: upstream `PROTOCOL.md` (peetzweg/opendisplay), written against `pv` 3. This section records what this sender implements. Cross-checked against upstream's revision of 2026-10-01 (`hello.panel` additive) in M0.

### 7.1 Transport binding (USB via usbmuxd)
Unix socket `/var/run/usbmuxd`. Each message: 16-byte little-endian header + XML plist payload.
```
[uint32 LE length incl. header][uint32 LE version = 1][uint32 LE message = 8 (plist)][uint32 LE tag]
[XML plist payload]
```
| MessageType | Fields sent | Reply |
|---|---|---|
| `Listen` | `ClientVersionString`, `ProgName` | `Result` 0, then a stream of `Attached` / `Detached` on the same socket |
| `Connect` | `DeviceID`, `PortNumber` = **htons(9000) = 10275** | `Result` 0 → socket becomes a raw byte pipe to the device port |
| `ListDevices` | — | `DeviceList` (used by `odx11 list`) |

| `Result` | Meaning | Daemon action |
|---|---|---|
| 0 | OK | proceed |
| 2 | bad device | treat as detached |
| 3 | connection refused | app not open/foreground; redial in 3 s |

- `Attached.Properties.ConnectionType` must be `"USB"`; ignore network-paired entries.
- `Attached.Properties.SerialNumber` is the UDID; `hello.id` (per-install UUID) is the config key.
- `Listen` holds its own socket; every `Connect` uses a fresh socket.
- Device must be trusted (`idevicepair pair`) and unlocked — confirmed in M0. With the app installed but not foreground, `Connect` returns `Result` 3.

### 7.2 Framing and demux
```
every message, both directions:  [uint32 BE payload length][payload]
receiver → sender:  always JSON, 1 ≤ length < 2^20
sender → receiver:  JSON control  iff  length < 32768  AND  payload[0] == '{'  AND  no 0x00 byte
                    otherwise video (Annex B always contains 0x00)
```
TCP_NODELAY on the usbmux socket. Reassemble across reads; never assume one read = one frame.

### 7.3 Session sequence
```
→ usbmuxd Connect(device, 9000)
← hello {panel, pixelsWide, pixelsHigh, scale, device, id, pv, ...}  first message, always
→ welcome {pv: 2, min: 1}                                         reply to EVERY hello
   display.attach(hello) → geometry → pipeline.start(geometry)
→ video: IDR (SPS PPS IDR-slice)                                  first frame always IDR
→ video: P ... P
↔ ping every 2 s, both directions
← ping {t}            → pong {t: <echoed>, mt: <now ms>}
← touch / scroll      → input.py
← kf                  → next frame IDR
← hello (rotation)    → debounce 300 ms → rebuild display + pipeline → welcome → IDR
← sleeping | closing | EOF | > 5 s silence → §8.4
```

### 7.4 Message handling
| Direction | `type` | Handling |
|---|---|---|
| ← | `hello` | Size/rebuild display from `panel` (§8.2); reply `welcome`; repeats debounced 300 ms. Ignore `cursorPort` (UDP cannot ride usbmuxd) and the `hevc` entry of `videoCaps` (HD 530 has no HEVC encoder) |
| ← | `ping` | Reply `pong`, echo `t` unchanged, add `mt` (sender ms since epoch) |
| ← | `touch` | §7.6 |
| ← | `scroll` | §7.6 |
| ← | `kf` | Force IDR on next frame |
| ← | `stats` | Log one line prefixed `PHONE-STATS` |
| ← | `sleeping` | Session ends; display kept for grace; redial (§8.4) |
| ← | `closing` | Session ends; immediate teardown; redial at 3 s while attached |
| ← | `pencil`, `proximity` | Not sent to us while `welcome.pv = 2` (receiver degrades stylus to `touch`) |
| ← | unknown | Ignore; log once per type, not per message |
| → | `welcome` | `{pv: 2, min: 1}` until M5 |
| → | `pong` | As above |
| → | `ping` | Every 2 s with health fields `capFps` (AUs/s since the last ping), `encDrops` (raw frames dropped by the leaky queue), `netDrops` (always 0: encoded frames are never dropped) |
| → | video | One AU per frame behind the telemetry prefix `{"cap":<ms>,"snd":<ms>}` (`cap` from the buffer's capture timestamp) — gives the receiver real `e2e`/`enc` stats |
| → | `cursor`, `cursorImg` | Sent (0.3.0, `odx11/cursor.py`): position normalised to the video at up to `[cursor] max_rate_hz` (120) per second, deduplicated, trailing update after bursts, `v:0` off the virtual monitor; the sprite (XFixes, PNG ≤ 24000 bytes) on every *image* change and with every new stream. Capture no longer draws the cursor (`show_pointer = false`), so pointer motion alone never wakes the paused stream |
| → | `streamConfig` | Not sent in v1; receivers assume legacy H.264 without it |

### 7.5 Video stream contract
- H.264, Annex B, **4-byte start codes only** (`00 00 00 01`).
- **One access unit per wire frame**; all slices of a picture together.
- **Every IDR is preceded by the current SPS and PPS** in the same wire frame.
- No B-frames; no timestamps on the wire; receiver displays in arrival order.
- First frame after every (re)connect is an IDR; `kf` makes the next frame an IDR.
- Resolution change = new SPS/PPS + IDR; receiver takes size from the SPS, not `hello`.
- AUD NALs (type 9) stripped; SEI allowed.
- Defaults: **60 fps** (0.3.0; capped at `hello.displayMaxFrameRate`, absent = 60), CQP (the only rate control this encoder offers, §4). Even dimensions, multiples of 16. Measured at 1568×1088 on the HD 530: 30 fps 25.6 % / 60 fps 48.4 % of one core while the screen changes — capture 1.5 %, software BGRx→NV12 ~32 %, encode upload ~13 %; the encoder takes only NV12/YV12/I420 and the iHD driver has no VPP, so the conversion stays in software until KMS zero-copy capture (v2). Idle cost is unchanged (the gate pauses capture).

### 7.6 Input mapping
`G = (gx, gy, gw, gh)` = virtual output geometry in X root coordinates, re-queried after every xrandr change (post-rotation).
| Message | Mapping |
|---|---|
| `touch` began | motion to `(gx + x·(gw−1), gy + y·(gh−1))`; **no press yet** — remember start point and time |
| `touch` moved, within `tap_slop_px` of start | motion only |
| `touch` moved, beyond `tap_slop_px` | motion to start, button 1 press, motion to here — a drag |
| `touch` ended, never left the slop | click at the start point: button 3 if held ≥ `long_press_ms`, else button 1 |
| `touch` ended, dragging | motion, button 1 release |
| `touch` cancelled, never left the slop | nothing — a two-finger scroll is starting; clicking would hit whatever was under finger one |
| `touch` cancelled, dragging | motion, button 1 release |
| `scroll` dy > 0 | button 4 (fingers down → content down → scroll up) per 40 px accumulated |
| `scroll` dy < 0 | button 5 per 40 px |
| `scroll` dx > 0 / dx < 0 | button 6 / button 7 per 40 px |
Why the deferred press: the receiver reports a two-finger scroll as a one-finger `began` followed by `cancelled` (upstream's Mac sender documents this). An X11 press cannot be retracted, so pressing on `began` turned every scroll into a click; macOS gets around it with a zero click-count release, X has no equivalent. Right-click fires on lift rather than mid-hold so input needs no timer of its own (timers stay in `daemon.py`). Upstream has no right-click at all; long-press is this sender's addition.
`touch.x/y` are normalised 0..1 against the video; `scroll.dx/dy` are **video pixels** with natural-scrolling sign. Scroll applies at the current pointer (last touch position). Step size is configurable.

## 8. Display, capture and lifecycle
### 8.1 Virtual monitor (forced connector)
| Fact | Value |
|---|---|
| GPU / driver | Intel HD 530, i915, Xorg modesetting driver |
| DRM card | `card1` (debugfs `dri/1`) |
| Rear video ports | 3 DDIs, each exposed as `DP-n` + `HDMI-A-n` |
| Main monitor connector | `HDMI-A-3` / xrandr `HDMI-3` — DP++ port driven through a passive DP→HDMI adapter |
| Virtual connector | `HDMI-A-1` / xrandr `HDMI-1` (verified M0); `HDMI-A-2` is the spare alternative |
| Force toggle | `/sys/class/drm/card1-HDMI-A-1/status` ← `on` / `detect` |
| EDID (runtime) | debugfs `/sys/kernel/debug/dri/1/HDMI-A-1/edid_override` — node exists, but writes fail with EPERM under Secure Boot lockdown (`integrity`). Not used. |
| EDID (boot-time, not set up) | `drm.edid_firmware=HDMI-A-1:edid/odx11.bin` on the kernel command line; blob from `edid/make_edid.py` |
| Without EDID | Forced connector offers 1024×768, 800×600, 848×480, 640×480 |
| Cinnamon on hotplug | Enables the new output itself (seen at `1024x768+1920+0`, right of main) before our xrandr runs; our later xrandr call is not reverted within 15 s |

Attach: `cvt -r` + `--newmode`/`--addmode` on the still-disconnected output → force `on` (sysfs) → wait (RandR events) for connected, then up to `settle_timeout_s` for Cinnamon to enable it from its saved layout → if the size differs, `--mode`/`--rotate` in place; if Cinnamon left it off, `--mode --rotate --pos` from `[devices]` defaults → re-query geometry → hand to pipeline and input.
Detach: stop pipeline → `detect` (sysfs) → wait for disconnected → `--off` → `--delmode`/`--rmmode`. **Unforce before removing the mode**: while the connector is still forced, Cinnamon re-enables an output we switch off (from its saved layout), the mode is then in use and `--rmmode` fails — M1 leaked a mode exactly this way.

### 8.2 Mode sizing
Panel size comes from `hello.panel.pixelsWide/High` (upstream §6.7); fall back to the deprecated `hello.pixelsWide/High` only when `panel` is absent or malformed. Both are in the current orientation, so swap to landscape before sizing.
```
logical_w = floor16(panel_landscape_w / divisor)      divisor default 1.5
logical_h = floor16(panel_landscape_h / divisor)
rotation  = normal if hello is landscape else left
```
| iPad panel (landscape) | Logical mode (÷1.5) |
|---|---|
| 2048 × 1536 | 1360 × 1024 |
| 2160 × 1620 | 1440 × 1072 |
| 2360 × 1640 | 1568 × 1088 ← the target iPad (Air 5th gen) |
Stream encodes at the logical size; the iPad scales up. Divisor and an absolute override live in config.

### 8.3 Capture and encode pipeline
```
ximagesrc display-name=:0 startx=gx starty=gy endx=gx+gw-1 endy=gy+gh-1
          use-damage=false show-pointer=true
  ! video/x-raw,framerate=30/1
  ! queue leaky=downstream max-size-buffers=1
  ! videoconvert n-threads=2 ! video/x-raw,format=NV12
  ! vaapih264enc tune=low-power rate-control=cqp max-bframes=0 keyframe-period=1000000
  ! h264parse config-interval=-1
  ! video/x-h264,stream-format=byte-stream,alignment=au
  ! appsink name=out sync=false emit-signals=true
```
Verified in M0 on Mint's GStreamer 1.24 with the iHD driver (gstreamer1.0-vaapi): 1360×1024 at 30 fps costs ~19 % of one core end to end; output has 4-byte start codes only and SPS+PPS before every IDR; `h264parse` inserts AUDs, which `annexb.py` strips. `keyframe-period=0` does **not** disable periodic IDRs (it means auto, one per 30 frames); a large value does (1 000 000 gave one IDR in 2500 frames). IDRs otherwise come from `kf`, reconnect and resize. `show-pointer=true` bakes the cursor in until M4. Idle gating per §4.

### 8.4 Session lifecycle
```
IDLE ──Attached(USB)──▶ DIALING
DIALING ──Result 3──▶ wait 3 s ──▶ DIALING            (app not open)
DIALING ──Result 0──▶ AWAIT_HELLO ──hello──▶ STREAMING
STREAMING ──EOF / >5 s silence / sleeping──▶ GRACE    (display kept, redial every 3 s)
GRACE ──hello, same id──▶ STREAMING                   (no xrandr change)
GRACE ──10 s elapsed──▶ teardown ──▶ DIALING
STREAMING ──closing──▶ teardown ──▶ DIALING
any ──Detached──▶ teardown ──▶ IDLE                    (cable pull = intent, no grace)
SIGTERM / crash ──▶ teardown (atexit + ExecStopPost)
any ──user disables the monitor──▶ teardown ──▶ PAUSED   (no redial until replug)
```
As built (M2): redial every `redial_s` (3 s) while the app is closed, every `redial_grace_s` (1 s) during grace so an app switch resumes quickly; a dial that connects during grace keeps the grace timer running until `hello`. A resumed session re-attaches with no xrandr change. Rotation hellos are debounced by `rotation_debounce_ms`. usbmuxd is started by udev on plug-in and exits with the last device, deleting its socket: the daemon watches that path with a Gio file monitor (inotify) and treats the Listen socket closing as every device detaching. Live-verified with fake receivers: resume within grace with no display change, teardown exactly at grace expiry; SIGKILL leaves the connector unforced within 1.5 s and the service restarts and reconnects.

### 8.5 Privileged helper
*[superseded 2026-10-14] Replaced in M7 by the udev group rule (§8.7) — the helper, its allowlist and the sudoers rule are deleted. Kept for history: the M1–M3 implementation.*
`/usr/local/sbin/odx11-connector {on|detect|edid}` — reads the single allowed connector from `/etc/odx11/connector`, refuses anything else, writes only the sysfs status file and the debugfs EDID override. `edid` takes the blob on **stdin**, never a path: a root process reading a caller-named file would leak any file's bytes back out through xrandr's EDID property. The blob is checked for the EDID header and 128-byte block size. The daemon refuses to start if `[display] connector` differs from the allowlist.
Sudoers: `<user> ALL=(root) NOPASSWD: /usr/local/sbin/odx11-connector`

### 8.6 Config
`~/.config/odx11/config.toml`, overridable with `--config`. **Every tunable lives here**; code keeps only values the protocol or hardware fixes. Sections: `[transport]` (usbmuxd socket, device port), `[display]` (connector, main output, divisor, mode refresh, helper path, connect timeout), `[video]` (fps, CQP `qp`, keyframe period, pointer, convert threads), `[input]` (scroll step), `[session]` (ping interval ≤ 2 s, silence timeout, grace, redial, grace redial, rotation debounce), and `[devices.<hello.id>]` tables with `side` (left/right/above/below), `align` (start/center/end), optional `mode` override. Unknown keys are rejected. `config.example.toml` documents every key at its default, and a unit test keeps the two identical.

### 8.7 Packaging and installation (design approved 2026-10-14)
Goal (§2): install by double-clicking a `.deb`, password in the desktop's dialog, nothing else. One binary package, `opendisplay-x11`, `Architecture: all` (pure Python + data files), native source format, built with debhelper + `dh-python`/pybuild from `pyproject.toml`.

**Privilege model ✔ (verified 2026-10-14).** The root helper, `/etc/odx11/connector` and the sudoers rule are **deleted**. A udev rule makes the HDMI connectors' `status` attribute group-writable; the daemon writes `on`/`detect` itself. Verified on this machine: with group write on `card1-HDMI-A-1/status`, a non-root `echo on` forced the connector and X created the output; `detect` removed it. Nothing runs as root at runtime.
```
# /usr/lib/udev/rules.d/70-opendisplay-x11.rules
ACTION=="add|change", SUBSYSTEM=="drm", KERNEL=="card*-HDMI-A-*", \
  RUN+="/bin/chgrp plugdev /sys%p/status", RUN+="/bin/chmod g+w /sys%p/status"
```
- **Scope: every HDMI-A connector, not one allowlisted port.** Why that is acceptable: any X client of the same user can already switch any monitor on or off with `xrandr`, so write access to a connector's force switch adds no real capability; and the port choice can then live in the *user's* config, picked in the GUI, with no root-owned file per machine. `DP-n` is never covered (forcing it fails link training; §8.1). The daemon still refuses any connector that is not `HDMI-A-n`.
- **Group ✔ `plugdev` (decided 2026-10-14).** *Correction (2026-10-14): only the account created by the OS installer is in `plugdev` (Mint/Ubuntu/Debian installers add it); accounts added later via Users and Groups get `sudo` at most (AccountsService → `adduser`, no extra groups). So it works with no setup for the installing user on any Debian-family system; each additional account needs a one-time group change (admin password, then log out/in), which the setup checklist explains.* Established for user access to pluggable hardware (libgphoto2 cameras, OpenOCD probes, FIDO keys); the modern `TAG+="uaccess"` cannot apply because it sets ACLs on `/dev` nodes and our target is a `/sys` attribute. The group name is one packaging variable, so an RPM/Arch build (no `plugdev` there) can use a dedicated group. Debian/Ubuntu/Mint put desktop users in `plugdev` ("access to pluggable devices") by default, so the common case needs **no group change and no re-login**. Alternative: a dedicated `odx11` group created by `postinst` — cleaner naming, but every user must be added (password dialog in the GUI) and log out/in before it works. Either way the GUI detects a missing membership and offers the fix.

**Files.**
| Path | What |
|---|---|
| `/usr/lib/python3/dist-packages/odx11/` | the package (pybuild) |
| `/usr/bin/odx11` | CLI entry point — developers/diagnostics only; also `daemon`, `autostart`, `release` subcommands used by the files below |
| `/usr/lib/systemd/user/odx11.service` | `ExecStart=/usr/bin/odx11 daemon`, `ExecStopPost=/usr/bin/odx11 release` (writes `detect` — always safe, no root), `Restart=on-failure`; no `[Install]` |
| `/etc/xdg/autostart/odx11.desktop` | `Exec=/usr/bin/odx11 autostart`: imports `DISPLAY`/`XAUTHORITY` into the user manager (so bare window managers work too), then starts the service if `[service] enabled` (default **true**) — desktop-agnostic XDG autostart |
| `/usr/lib/udev/rules.d/70-opendisplay-x11.rules` | the privilege rule above |
| `/usr/share/applications/…desktop` + icon | the GUI (M8) in the app menu, "OpenDisplay for X11", category **Utility** (Mint: Accessories; decided 2026-10-14) |
| `/usr/share/doc/opendisplay-x11/` | README, `config.example.toml` |
No file under `/etc` except the autostart entry (a conffile); no per-user state is written by the package.

**Dependencies.** `Depends: python3 (>= 3.11), python3-gi, python3-xlib, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, gstreamer1.0-plugins-base` (videoconvert, appsink) `, gstreamer1.0-plugins-good` (ximagesrc) `, gstreamer1.0-plugins-bad` (h264parse) `, gstreamer1.0-vaapi, usbmuxd, libimobiledevice-utils, x11-xserver-utils` (xrandr) `, xcvt` (cvt). `Recommends: intel-media-va-driver | va-driver-all`. GUI (M8) adds `gir1.2-gtk-4.0 (>= 4.14), gir1.2-adw-1 (>= 1.5), python3-qrcode` (notifications go over D-Bus with Gio — no libnotify). Build: `debhelper-compat (= 13), dh-python, pybuild-plugin-pyproject, python3-all, python3-setuptools, python3-pytest` — unit tests run during the build (they need no X, iPad or GPU).

**Maintainer scripts.**
- `postinst`: `udevadm control --reload` and `udevadm trigger --subsystem-match=drm --action=change`, so permissions apply **without a reboot**. (Group: nothing to do for `plugdev`; `addgroup --system odx11` if the dedicated group wins.)
- `prerm` (remove/upgrade): send SIGTERM to running `odx11 daemon` processes so each unforces its connector before files disappear.
- `postrm` (remove/purge): reload udev, and reset group/mode on the HDMI `status` files (`chgrp root`, `chmod g-w`) — removing a rule does not revert permissions already applied until reboot.
- No user's home is touched; the per-user config survives uninstall and reinstall.

**User journey.**
1. Download the `.deb`, double-click → the distro's package installer → password dialog → done. Dependencies arrive with it.
2. **Enabled by default ✔ (decided 2026-10-14):** the autostart entry starts the service at the next login; the GUI's "Start" button does it immediately. Off switch: the GUI (`[service] enabled = false`).
3. First run of the GUI (M8): shows the chosen spare port (first disconnected `HDMI-A-n`, changeable), the OpenDisplay TestFlight link as a QR code to scan with the iPad, and "Plug in, unlock, tap Trust" with live pairing status. Missing group/encoder/port → a sentence and a fix button, never a command.
4. Updates: installing a newer `.deb` from any update manager (channel undecided — GitHub Releases or a PPA). **The running daemon restarts itself onto the new version** (decided 2026-10-14; a brief cut-off is accepted): it watches `/usr/share/opendisplay-x11/VERSION` (inotify), waits for dpkg's `install ok installed`, re-reads its unit, tears down cleanly and exits 75; the unit's `SuccessExitStatus=75` + `RestartForceExitStatus=75` restart it without logging a failure. Verified on hardware: 0.2.1 → 0.2.2 via apt, iPad back after ~6.5 s, no log-out. Developer runs from a source tree never self-restart. **Only when the service itself changed** (decided 2026-10-15): the build also installs `/usr/share/opendisplay-x11/SERVICE`, a sha256 of the daemon's modules (all `odx11/*.py` except the window-only `gui.py`, `settings.py`, `checks.py`) and its unit; an upgrade that leaves it unchanged is logged and does not restart, so window-only releases never blink the iPad. A test fails the build if a daemon-side module imports a window-only one. (Without an upgrade, the window's On/Off switch restarts it; logging out works too.)
5. Uninstall: the distro's software manager. The connector is released by `prerm`.

**Daemon changes this implies (M7 implementation).** `display.py` writes `/sys/class/drm/<card>-<connector>/status` directly (card found by connector name), with a clear error when it is not writable ("setup not finished: not in plugdev / udev rule missing"); `helper/`, `/etc/odx11`, sudoers and `helper_problem()` go; `odx11 autostart` and `odx11 release` subcommands; `[service] enabled` config key; `install.sh` becomes the developer path that builds and installs the `.deb`. Known edge: two users logged in at once on one machine would both dial the iPad — daemons should act only while their logind session is active (later).

**Migrating this machine (done 2026-10-14).** The current dev install (helper in `/usr/local/sbin`, `/etc/sudoers.d/odx11`, `~/.config/systemd/user/odx11.service` — which would *shadow* the packaged unit) is removed by `install.sh --uninstall` before the first `.deb` install.

### 8.8 GUI (design approved 2026-10-14; built in M8)
**Standards (as built).** App ID `io.github.cray2015.OpenDisplayX11` (reverse-DNS; desktop file, icon and AppStream ID share it; GApplication single instance). XDG Base Directory (config under `$XDG_CONFIG_HOME`, state under `$XDG_RUNTIME_DIR`), Desktop Entry spec, Icon Theme spec (hicolor scalable), AppStream (`appstreamcli validate` passes; one pedantic note on the upper-case ID, which GNOME's own apps share), GNOME HIG (header bar with primary menu, About dialog, standard symbolic status icons, sentence case, no Apply button, Ctrl+Q / Ctrl+W), accessible names on custom controls, gettext-wrapped strings with `ngettext` plurals, a man page, lintian clean. Project licence still undecided: AppStream says `LicenseRef-proprietary` until chosen.

**Shape ✔ (decided 2026-10-14): a normal window, no tray icon,** opened from the app menu as **OpenDisplay for X11**; status changes reach the user as desktop notifications. Identical on every X11 desktop (§2); nothing runs in the panel.

**Principles.**
- The daemon never depends on the GUI. Closing, crashing or never opening the window changes nothing.
- **No command channel into the daemon** (§ Security). The GUI *reads* the daemon's state and acts only through things that already exist: `systemctl --user` (start/stop/restart), the config file, `odx11 release`, `idevicepair`.
- Event-driven both ways: the daemon writes state only on transitions; the GUI watches it (inotify). Not running = zero cost.
- Plain words only. Every state a user can see says what is happening and, if needed, what to do — never an error code.

**Daemon → GUI: a state file.** On every transition the daemon atomically rewrites `$XDG_RUNTIME_DIR/odx11/state.json`: `state`, a plain-language `headline` and optional `action` (what the user should do), device name and model, panel and monitor size, idle/streaming, the last problem, version, and a timestamp. The GUI follows it with a Gio file monitor. Removed when the daemon exits; absent = "Off".

| Daemon situation | Headline shown | Action shown |
|---|---|---|
| service stopped | Off | Turn on |
| setup problem (`connector_problem`) | Setup needed | the sentence from `connector_problem`, with a fix button where one exists |
| no iPad attached | Waiting for your iPad | Plug it in with a USB cable |
| attached, dial refused | iPad connected | Unlock it and open the OpenDisplay app |
| streaming | Showing your desktop on *iPad Air* (1568×1088) | — (adds "idle" while the gate has paused capture) |
| grace | Connection lost — keeping the screen for *n* s | Reopen the app to resume |
| paused (monitor disabled in display settings) | Turned off in display settings | Unplug and replug the iPad to resume |

**Notifications (sent by the daemon) — as silent as possible (decided 2026-10-14): by default only when the user must act** (*Open OpenDisplay on your iPad*, *Setup needed*); *connected* / *disconnected* are off by default, since both are visible on the iPad itself. Through the freedesktop notification service (works on any desktop running a notification daemon), an outgoing D-Bus call — no new interface. One notification per meaningful transition, never repeated while the state holds: *iPad connected — desktop extended*; *Open OpenDisplay on your iPad* (once per plug-in, after the first refused dials); *iPad disconnected*; *Setup needed: …*. `[notifications] enabled` (default on) and per-kind switches in the config.

**Change-driven frames (0.3.2).** A paused stream only helps when *nothing* changes; a window that redraws twice a second (e.g. btop on the iPad's monitor) kept capture, conversion and encoding running at 60 fps — measured ~40 % of one core to show 2 changes a second. Capture is cheap (~1.5 % at 60 fps); conversion and encoding are not. So a probe in front of `videoconvert` passes frames only for `[video] change_tail_ms` (150) after a wake (damage on the iPad's monitor, touch, `kf`, a new stream) and drops raw frames otherwise (never encoded ones, §4); intentional drops are not reported as `encDrops`. Measured with btop on the iPad: 40 % → 10 % of one core. Continuous change (a video) still gets every frame.

**Minimized windows (0.3.3).** Under Cinnamon's compositor a minimized window stays mapped (for previews) and keeps sending damage: a minimized system monitor on the iPad's monitor woke capture twice a second (~5 % of one core for nothing visible). Damage from a window whose client has `WM_STATE` = Iconic or `_NET_WM_STATE_HIDDEN` no longer wakes it — checked only for windows overlapping the virtual monitor. Found by the benchmark's idle scenario.

**[superseded 2026-10-15 by 0.3.5]** **Change tail 50 ms + settle frame (0.3.4, decided 2026-10-15).** *Reverted to a 150 ms tail after hands-on use: the iPad visibly froze while moving over a context menu and caught up later. Measured on the PC side, capture was not at fault — the menu's map and every hover change woke the stream and frames went out 7–18 ms later, 3–4 per change. The iPad's hardware decoder evidently holds back the newest frame until more frames arrive, so ~3 frames per change left the final picture of a change stuck until the next change or the settle frame. 150 ms (~9 frames) flushes it; the settle frame stays as a safety net. Lesson: the benchmark measures sender CPU, not perceived smoothness — gating changes need a hands-on check on the iPad.* The 150 ms tail sent ~9 frames per change at 60 fps, mostly repeats; the compositor shows a change within one refresh. Now: 50 ms of frames after each wake, then one frame once quiet for `change_settle_ms` (300) to catch compositor-only animations (fades, menus) that finish after the tail without damage; the idle pause waits for a pending settle frame. Measured: 1/s 9.1 → 6.0 %, 2/s 15.7 → 8.6 %, 5/s 35.1 → 13.9 %, 10/s 46.4 → 25.1 % of one core; continuous unchanged (45.9 %). Re-checked timers treat "within 1 ms" as due — a float remainder of ~1e-17 s otherwise rescheduled forever (found by the tests).

**Measured performance (2026-10-15, `tools/perf_bench.py`, 3 × 25 s each, README "Performance").** CPU (one core) by how often the iPad's screen changes: idle 0.1 %, 1/s 9.1 %, 2/s 15.7 %, 5/s 35.1 %, 10/s 46.4 %, 30/s 48.6 %; continuous 22.9 % at 30 fps, 45.9 % at 60 fps. Memory (PSS) flat at 56–60 MB. Latency ~10 ms median, 13.6–17.6 ms 95th percentile; encode ~9 ms. The 150 ms change tail sends ~9 frames per change at 60 fps, so cost saturates from ~7 changes/s — a shorter tail is the next lever (needs a visual check that no final state is missed). Raw data in `docs/perf/results.csv`; `tools/test_window.py` provides the fixed-rate changes.

**Performance presets (decided 2026-10-15, 0.3.1).** A *Performance* choice sits above the three settings that cost CPU, power or USB bandwidth: **Power saver** (30 fps, QP 30, pause after ½ s — ~¼ of one core while the screen changes on the HD 530), **Balanced** (default: 60 fps, QP 26, 1 s — ~½ core), **Best quality** (60 fps, QP 22, 2 s — ~½ core, more data), **Custom**. A preset greys those rows out and shows its values; Custom unlocks them. Presets live in code (`config.PRESETS`); `[video] preset` selects one and the user's own `fps`/`qp`/`idle_pause_s` are kept for Custom. Text size is deliberately not part of a preset (a readability choice).

**Per-iPad capabilities (automatic, 0.3.1).** Each `hello`'s decode ceiling is honoured: `maxEncodeWide/High` and the h264 `videoCaps` entry (max size, frame rate, pixel rate — both must be satisfied, upstream §6.5) shrink the mode keeping its shape and cap the frame rate, on top of `displayMaxFrameRate`. HEVC offers are ignored (the HD 530 cannot encode it); the protocol has no colour negotiation, every receiver decodes 8-bit H.264.

**Per-iPad preferences: none (decided 2026-10-15).** One set of settings for every iPad; only capabilities adapt per iPad (automatically, above). `[devices.<id>]` keeps its existing placement fallback and mode override for hand edits; the window does not expose per-iPad settings.

**The window** (layout revised 2026-10-14 after review: Start at login under Settings; "Frame rate", "Right-click sensitivity", "Screen timeout"; no settings-file row — advanced keys are documented in the README/man page; the checklist under Help; *Remove the virtual monitor now* last, as a red destructive-action button with no confirmation since turning it back on undoes it; the streaming headline reads "Show Desktop on iPad"). Original design:
1. **Status** (top): the headline, the action, the device, and an **On/Off** switch (`systemctl --user start/stop`) plus **Start at login** (`[service] enabled`).
2. **Settings** — only what people change, in plain words: *Text size* (the divisor, showing the resulting resolution for the last iPad seen), *Sharpness* (qp), *Smoothness* (fps), *Pause when nothing changes after* (idle), *Right-click hold time*, *Keep the screen after a disconnect for* (grace), and *Video port* (a list of the HDMI ports marked free / in use / has a monitor). **Changes apply automatically** (decided 2026-10-14, no Apply button): the window writes the config (sliders ~1 s after release) and the daemon, which watches its config file, applies each change at the lightest level it needs — live (input, idle, grace, notifications), a pipeline restart with a fresh IDR (sharpness, smoothness), or a display rebuild (text size, video port; marked "iPad will blink"). *Open the settings file* for everything else.
3. **Setup checklist** (expanded automatically when something is wrong, collapsed when all is green): X11 session · free video port · permission (plugdev + rule) · video encoder · iPad paired (**Pair** button runs `idevicepair pair`; the iPad asks to Trust) · OpenDisplay app on the iPad (**QR code** of the TestFlight link to scan with the iPad, plus the link).
4. **Help** (bottom): *Copy diagnostic report* (versions, checklist, state, last 200 log lines → clipboard), *Remove the virtual monitor now* (`odx11 release`), and a link to the README.

**Zero settings by default.** `[display] connector` defaults to **`auto`**: the daemon picks the first free HDMI-A port at each start (keeping the one it already forced). A fresh install on a machine with a free port needs no configuration at all; the GUI's port list is for the rare override.

**Tech ✔ (decided 2026-10-14): GTK 4 + libadwaita via PyGObject** — GTK 3 was the first pick (the toolkit of Cinnamon/MATE/XFCE, bindings already present) but is in maintenance mode; GTK 4 is the actively developed series. libadwaita's settings rows, switches, toasts and status pages match the design closely, and **Mint patches libadwaita to follow the system theme** (`1.5.0-1mint1`: "use the current system theme for style info"), so it looks native there; on other desktops/distros it is GNOME-styled (Adwaita). Floor: **GTK 4.14 / libadwaita 1.5** (Mint 22, Ubuntu 24.04) — no widgets newer than that; Debian 12 (libadwaita 1.2) is out. The window is its own process; the daemon never imports GTK. `python3-qrcode` for the QR matrix drawn with cairo (no image library). New modules: `odx11/state.py` (state file write/read — pure, tested), `odx11/settings.py` (UI values ↔ config, writing TOML — pure, tested), `odx11/notify.py` (the D-Bus call), `odx11/gui.py` (the window). Entry point `odx11 settings`; packaged as `/usr/share/applications/opendisplay-x11.desktop` with an SVG icon. Writing the config from the GUI rewrites the file in a canonical form (comments in a hand-edited file are not preserved — the file says so in its header).

## 9. Milestones
- [x] M0 — Spike: transport, forced connector, VA-API encode and end-to-end test pattern proven by hand; no product code
- [x] M1 — MVP CLI: `odx11 run` extends the desktop to the iPad over USB with touch and scroll
- [ ] M2 — Seamless daemon: systemd user service, usbmuxd attach/detach, grace, rotation, per-device placement
- [ ] M3 — Hardening: backpressure with IDR recovery, liveness, CPU budget measured and met, 2-hour soak
- [x] M4 — Cursor channel via XFixes (`cursor` + `cursorImg`) — built 2026-10-15 (0.3.0) for a cursor that moves at input speed
- [ ] M5 (backlog) — Apple Pencil via uinput tablet device, `welcome.pv` raised to 3
- [ ] M6 (backlog) — Wi-Fi transport via Avahi `_opensidecar._tcp`
- [ ] M7 — Packaging: one `.deb`, udev privilege (no root at runtime), install and uninstall with no terminal (§8.7)
- [ ] M8 — GUI: status, on/off, settings, first-run (port, pairing, QR to the app), notifications, diagnostics — desktop-agnostic GTK; design to be approved
- [ ] M9 — Software Manager listing: AppStream metadata (name, icon, screenshots) and a package repository (PPA or signed apt repo), so OpenDisplay for X11 is found, installed, updated and removed in the distro's software manager — the last step to no terminal; decided after M8
- [ ] M10 — GitHub Actions: every pull request verified against the project's requirements and mergeable only with the owner's approval; tagged releases publish the .deb with its changelog (requested 2026-10-15)

## 10. Acceptance criteria
1. [x] M0: OpenDisplay receiver installed on the iPad via TestFlight; iPad model and iPadOS version recorded in §11
2. [x] M0: `idevice_id -l` lists the iPad and `idevicepair validate` succeeds using stock Mint packages
3. [x] M0: With the app open, `iproxy 9000 9000` + `nc localhost 9000 | xxd` shows a length-prefixed `hello`; panel size recorded in §11
4. [x] M0: Main and spare connector names identified from `/sys/class/drm` and `xrandr` and recorded in §8.1
5. [x] M0: Writing `on` to the spare HDMI alias makes `xrandr` report it connected, and a §8.2 mode enables beside the main monitor with a clean `dmesg`
6. [x] M0: Writing `detect` removes the virtual monitor and Cinnamon moves its windows back to the main monitor
7. [x] M0: `vainfo` lists an H.264 encode entrypoint on the HD 530; driver package recorded in §11
8. [x] M0: GStreamer captures the virtual monitor's region and VA-API encodes it at 30 fps; CPU use recorded in §11
9. [x] M0: A throwaway script streams a test pattern through `iproxy` and the iPad displays it
10. [x] M1: `odx11 run` connects via usbmuxd without `iproxy`, completes `hello`/`welcome`, and the iPad shows the extended desktop
11. [x] M1: A window dragged onto the virtual monitor appears on the iPad and stays interactive
12. [x] M1: Tap clicks, drag drags, and two-finger scroll scrolls at the touched position on the virtual monitor
13. [x] M1: A `kf` request makes the next sent frame an IDR prefixed with SPS and PPS (unit test and live)
14. [x] M1: Ctrl-C tears down the virtual monitor and windows return to the main monitor
15. [x] M1: A long press right-clicks on lift, and a two-finger scroll never clicks what is under the first finger
16. [ ] M2: With the user service enabled, plugging in the iPad and opening the app extends the desktop with no command run
17. [x] M2: Pulling the cable tears the display down immediately; an app switch shorter than the grace period resumes without moving windows
18. [x] M2: Quitting the app tears down immediately; locking and unlocking the iPad within the grace period resumes the session
19. [x] M2: Rotating the iPad rebuilds the virtual monitor in the new orientation within 1 s
20. [ ] M2: The iPad monitor returns to its configured placement after a reconnect and after a reboot
21. [x] M2: Killing the daemon with SIGKILL still leaves no forced connector behind (ExecStopPost)
22. [x] M3: Unit tests pass for framing, demux rule, Annex B normalisation, mode sizing, coordinate mapping and state transitions
23. [ ] M3: Idle desktop for 10 minutes keeps daemon plus pipeline CPU under the §11 budget
24. [ ] M3: A 2-hour working session completes with no stall longer than 2 s and no visible corruption
25. [x] M7: `dpkg-buildpackage` builds `opendisplay-x11_*.deb` with the unit tests passing and no lintian errors
26. [x] M7: Double-clicking the .deb in Mint's package installer installs it with only a password dialog; all dependencies arrive automatically
27. [ ] M7: After install and one log-in, plugging in the iPad and opening the app extends the desktop with no command run
28. [x] M7: No process runs as root while streaming, and forcing the connector works without sudo
29. [ ] M7: Removing the package leaves no forced connector, no running daemon and no files outside the user's home
30. [x] M8: The app menu has "OpenDisplay for X11"; its window shows the live state within 1 s and follows changes without reopening
31. [x] M8: Every daemon state is shown in plain words, with what to do when action is needed
32. [x] M8: On/Off and Start-at-login work from the window; settings apply (service restarted) and persist
33. [x] M8: On a fresh install with a free port, nothing must be configured: the port is chosen automatically
34. [ ] M8: Pairing the iPad and getting the OpenDisplay app (QR code) are possible from the window
35. [ ] M8: Notifications for connect, needs-action and problems arrive once per change and can be switched off
36. [ ] M8: "Copy diagnostic report" and "Remove the virtual monitor now" work
37. [ ] M8: The GUI adds no listening socket or daemon command channel; closing it changes nothing; idle CPU is unchanged
38. [ ] M10: A CI workflow runs on every pull request and push to main: unit tests, the .deb build (tests inside), lintian clean, desktop-file-validate, appstreamcli validate, SPDX headers on every source file, versions in step, DCO sign-off on every commit
39. [ ] M10: main only changes through pull requests that pass CI and are approved by the owner (CODEOWNERS + branch protection) — needs the repo public or GitHub Pro (checked 2026-10-15: unavailable while private on the free plan)
40. [ ] M10: Pushing a tag vX.Y.Z builds the .deb on Ubuntu 24.04 and publishes a GitHub Release with the .deb and that version's debian/changelog entry as notes; it refuses a tag that does not match debian/changelog
<!-- Checkbox rules — this file is the ONLY source the Trello sync reads.
     `- [ ]` for unordered work, `N. [ ]` where order is the point. Both parse
     the same; the list marker is stripped before hashing, so renumbering or
     inserting a step never strands a card.
     One physical line per task — only the first line reaches the card.
     Never reword a pending task in place: card identity is a hash of the task
     text, so an edit strands the old card and creates a new one. To retire a
     task, check it off; to change its meaning, check the old one and add a
     new line. -->

## 11. Open questions
- **Which iPad model and iPadOS version?** — sets panel size, §8.2 mode and whether the receiver installs at all (needs iPadOS 15+ upstream, 16+ per the Linux fork). Settled by AC 1 and 3. **Answered (M0):** iPad Air 5th gen (`iPad13,16`, M1), iPadOS 27.0.1, OpenDisplay build 178 (`com.peetzweg.opensidecar.ios`). `hello.panel` 2360×1640 @ scale 2, `pv` 3, `displayMaxFrameRate` 60, `videoCaps` h264 + hevc (≤ 2360), `cursorPort` 9001.
- **Does forcing the HDMI alias work on this board?** — strong evidence from a Mint X11 setup doing the same; settled by AC 5. If not: DP dummy plug, with `xrandr --off` replacing `detect`. **Answered (M0): yes** — `on` on `HDMI-A-1` → xrandr connected, 1024×768 enabled right of `HDMI-3`, no drm/i915 lines in `dmesg`; `detect` removed it. The §8.2 mode 1568×1088 (`cvt -r` + `--newmode`/`--addmode`) then enabled right of `HDMI-3`, again with a clean `dmesg`.
- **EDID delivery: runtime debugfs `edid_override` or boot-time `drm.edid_firmware`?** — prefer runtime for per-device sizing without reboot; settled in M0 by checking the debugfs node exists and is honoured on a forced connector. **Partly answered (M0):** `edid_override` exists under `dri/1/HDMI-A-1`; honoured-on-force still to test. **Answered (M2): neither, for now** — runtime is blocked by Secure Boot lockdown; boot-time was declined (GRUB edit + reboot, one fixed mode for all iPads). The monitor is unnamed (`unknown`) and that is accepted.
- **Does Cinnamon's settings daemon react to the hotplug and fight our xrandr call?** — if it auto-applies its own layout, either apply ours after it settles or let it place the monitor and only correct. Settled in M0/M1 by watching `xrandr --listmonitors` after `on`. **Answered (M0): it reacts** — the output came up active at `1024x768+1920+0` before our xrandr call; our subsequent call was not reverted within 15 s. Longer-run interference (and whether `monitors.xml` re-applies) still to watch in M1. **Answered (M2):** it re-applies the user's saved layout (`cinnamon-monitors.xml`) on every hotplug when the mode exists, and re-enables an output switched off while still forced. Resolved by letting Cinnamon own placement and unforcing before mode removal (§4, §8.1).
- **Does usbmuxd `Connect` require pairing?** — the Linux fork says paired and unlocked; confirm in AC 2–3. **Answered (M0):** pairing validated with stock packages and `Connect` succeeded; `Result` 3 when the app is not foreground.
- **Divisor default** — 1.5 is a guess; tune by eye on the actual iPad.
- **CPU budget** — no number until AC 8 measures the baseline; set the M3 target from it. **Set (M3): idle ≤ 2 % of one core** for daemon + pipeline (measured 1.0 % over 30 s with the gate; `tools/idle_cpu.sh` runs the 10-minute AC 23 check). Active streaming ≈ 20–23 % is not budgeted. **Baseline (M0, main-screen region as stand-in):** capture + `videoconvert` + VA-API encode at 1360×1024/30 fps ≈ 19 % of one core; x264 ultrafast ≈ 31 %. **On the virtual output (AC 8):** 1568×1088/30 fps ≈ 23 % of one core (~6 % of the CPU) with the screen static; 600 frames of an empty desktop encode to 47 KB. Driver: `intel-media-va-driver` 24.1.0 (iHD, free build); `i965-va-driver` 2.4.1 is installed but its encoder aborts.
- **Bitrate and fps defaults** — start 30 fps / 12 Mb/s; revisit after AC 10. **Superseded (M0):** CQP only on this driver (§4); tune `init-qp`/quality instead of bitrate. Receiver reported 29 fps, 0 stalls, 2 ms RTT on the test pattern.
- **Is `use-damage=true` enough to keep idle cost near zero?** — ximagesrc still emits frames at the set rate; if idle encode cost is visible, pause the pipeline after N seconds without damage (receiver liveness is covered by pings). **Answered (M0): no** — `use-damage=true` costs more (35 % vs 19 %); design moved to an XDamage gate (§4).
- **Can CBR be unlocked?** — Low-power encode needs HuC firmware for bitrate control. Options: `i915.enable_guc=2` (loads HuC; kernel cmdline + reboot) or `intel-media-va-driver-non-free` (adds the full `EncSlice` entrypoint). Not needed while CQP works. **M0:** `huc_info` reports `HuC disabled`, so CBR on this box requires `i915.enable_guc=2`.
- **Receiver availability** — TestFlight-only today; if the public beta closes before an App Store release, the project blocks. Watch upstream releases.

## 12. Files
- `PROJECT_SPEC.md` — this file
- `CLAUDE.md` — build commands and invariants
- `README.md` — what it is, requirements, install, use, configuration, limitations, security, troubleshooting
- `install.sh` — developer install: machine checks, build the `.deb`, install it; `--check`, `--build`, `--uninstall`
- `debian/`, `packaging/` — the `.deb` (§8.7)
- `docs/architecture.d2` — architecture diagram source
- `pyproject.toml` — package metadata, pytest config (created in M1)
- `config.example.toml` — every config key at its default
- `odx11/` — daemon package (§6)
- `edid/make_edid.py` — EDID generator
- `tools/fake_receiver.py` — test receiver
- `tools/idle_cpu.sh` — AC 23 idle-CPU measurement
- `tests/` — unit tests
- `spike/` — M0 throwaway scripts (`m0_connector.sh`, `hello_probe.py`); not product code

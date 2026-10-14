# opendisplay-x11

Use an iPad as a wired, extended second monitor for a Linux X11 desktop —
plug in the cable, open the app, and the desktop extends onto it. Touch,
drag, long-press right-click and two-finger scroll work on the iPad.

It is an X11 *sender* for the [OpenDisplay](https://github.com/peetzweg/opendisplay)
protocol; the iPad runs the unmodified OpenDisplay app as the *receiver*.
Everything runs over the USB cable through the stock `usbmuxd` — no Wi-Fi,
no USB networking, no browser, no custom kernel.

Developed and verified on an HP EliteDesk 800 G3 DM (Intel HD 530) with
Linux Mint 22.3 Cinnamon and an iPad Air (5th gen). Design, decisions and
acceptance criteria: [`PROJECT_SPEC.md`](PROJECT_SPEC.md).

## How it works

```
iPad (OpenDisplay app, listens on :9000)
   ▲ USB cable — usbmuxd byte pipe: [len][H.264 frame] →   ← [len][JSON: hello/touch/scroll/ping]
   │
odx11 daemon (systemd --user)
   ├─ forces a spare HDMI connector "connected" (root helper) → X sees a new monitor
   ├─ captures that monitor's area (ximagesrc) → H.264 on the GPU (VA-API)
   ├─ touch/scroll → XTest pointer events on that monitor
   └─ pauses capture while nothing on the iPad's monitor changes
```

- **Virtual monitor:** the GPU's spare DP++ port is exposed as an HDMI
  connector; forcing it on makes X create a real monitor with nothing
  plugged in. Your desktop treats it like any other screen.
- **Size:** the monitor is the iPad's panel divided by `divisor` (1.5), so
  text stays readable; the iPad scales it up. 2360×1640 → 1568×1088.
- **Lifecycle:** usbmuxd reports the iPad plugged in → the daemon dials the
  app → on `hello` the monitor appears and streams. Unplugging removes it at
  once; an app switch or a lock shorter than 10 s keeps it, so windows don't
  reshuffle.
- **Idle:** capture pauses after 1 s with nothing changing on the iPad's
  monitor (per-window X damage, pointer motion, touch) — about 1 % of one CPU
  core idle vs ~20 % while streaming.

## Requirements

| | Needed | Notes |
|---|---|---|
| Session | X11 | Wayland is not supported (use [opendisplay-linux](https://github.com/tixwho/opendisplay-linux) there). Any X11 desktop or window manager. |
| GPU | Kernel DRM driver with a **spare HDMI / DP++ port** | Tested on Intel `i915`. `amdgpu` should work for the monitor; encoder settings untested. NVIDIA's proprietary driver cannot force a connector. Nothing may be plugged into the chosen port. |
| Encoder | VA-API H.264 via `gstreamer1.0-vaapi` | Intel: `intel-media-va-driver` (the free build is enough). |
| OS | systemd user session, Python ≥ 3.11, Debian/Ubuntu packages | Mint 22.3 / Ubuntu 24.04 tested. The installer uses `apt`. |
| iPad | iPadOS 15+ (upstream; the Linux fork states 16+), OpenDisplay app, a USB **data** cable | The app is a [TestFlight beta](https://testflight.apple.com/join/3NYaY11c). The iPad must be trusted (paired) and unlocked. |

## Install

```bash
git clone <this repo> && cd opendisplay-x11
bash install.sh --check          # reports what is missing; changes nothing
bash install.sh                  # installs (asks for sudo once, for the root helper)
```

`install.sh` checks the session, packages (add `--install-deps` to apt-install
missing ones), GPU driver, encoder and iPad pairing; picks the first free HDMI
connector (override with `--connector card1-HDMI-A-2`); installs:

| What | Where | Why |
|---|---|---|
| Root helper | `/usr/local/sbin/odx11-connector`, `/etc/odx11/connector`, `/etc/sudoers.d/odx11` | Forcing a connector needs root; the helper does only that, for one allowlisted connector |
| User service | `~/.config/systemd/user/odx11.service` | The daemon; unforces the connector on every exit, even SIGKILL |
| Autostart entry | `~/.config/autostart/odx11.desktop` | Starts the service once your desktop session is up (XDG autostart — honoured by GNOME, KDE, XFCE, MATE, Cinnamon, LXQt) |

First time with the iPad: plug it in, unlock it, tap **Trust**, then
`idevicepair validate` should succeed.

## Use

Plug in the iPad, unlock it, open OpenDisplay. That's it.

| On the iPad | Does |
|---|---|
| Tap | Left click (on lift) |
| Long-press (½ s), lift | Right click |
| Drag | Drag (starts after ~8 px of movement) |
| Two-finger scroll | Scroll wheel, natural direction |
| Rotate | The monitor rotates (portrait = same mode, rotated) |

Arrange the iPad monitor in your desktop's display settings. Cinnamon
remembers the arrangement across reconnects and reboots (verified); other
desktops that save monitor layouts should too (untested). Where nothing
remembers layouts, `[devices]` `side`/`align` in the config place it.

```bash
journalctl --user -u odx11 -f                 # what the daemon is doing
systemctl --user restart odx11                # after changing the config
python3 -m odx11 list                         # iPads usbmuxd sees
systemctl --user stop odx11 && python3 -m odx11 run -v   # foreground, verbose (debugging)
```

## Configure

Optional: `cp config.example.toml ~/.config/odx11/config.toml`. Every setting
is documented there with its default; unknown keys are rejected so typos fail
loudly. The ones people change:

| Setting | Default | Effect |
|---|---|---|
| `[display] divisor` | `1.5` | Bigger = larger text, fewer pixels on the iPad |
| `[video] qp` | `26` | Picture quality: lower = sharper, more bandwidth (0–51) |
| `[video] fps` | `30` | Frame rate while streaming |
| `[video] idle_pause_s` | `1.0` | Pause capture after this long with no change; `0` = always stream |
| `[input] long_press_ms` | `500` | Hold time for right-click |
| `[session] grace_s` | `10` | How long a dropped connection keeps the monitor |

## Limitations

**By design**
- **X11 only.** No Wayland.
- **USB only.** The Wi-Fi transport is not implemented (planned, M6).
- **One iPad at a time.** A second one is used when the first is unplugged.
- **The cursor is drawn into the video,** so it moves at video latency
  (~50 ms), not input latency (separate cursor channel planned, M4).
- **No Apple Pencil pressure/tilt.** The Pencil works as a finger (M5).
- **No audio, clipboard or mirror mode.**

**Hardware and platform**
- **Needs a spare, empty HDMI/DP++ port** on a DRM GPU (Intel/AMD). Without
  one there is no virtual monitor. A DP dummy plug is the fallback but then
  the monitor exists even without the iPad.
- **Encoding is tuned for Intel VA-API** (`vaapih264enc` in low-power CQP
  mode). On the HD 530 there is no bitrate control (needs HuC firmware), so
  bandwidth varies with content — irrelevant over USB. `gstreamer-vaapi` is
  deprecated upstream; a future GStreamer may need a different encoder.
- **Secure Boot:** kernel lockdown blocks the runtime EDID, so the monitor is
  listed as `unknown` in display settings. Everything else works. A boot-time
  EDID is possible (`edid/make_edid.py`, `drm.edid_firmware=`), not set up.
- **All iPads share one monitor identity,** so two different iPads cannot have
  different remembered placements.
- **Picture is scaled.** The desktop is smaller than the panel (÷ 1.5) and
  the iPad upscales it, so it is slightly soft. Lower `divisor` for sharper
  but smaller text.

**Behaviour**
- **Idle detection doesn't see what the compositor draws itself** (panels,
  OSDs, notifications). If you move a panel onto the iPad monitor, its changes
  won't wake a paused stream until something else does.
- **Locking the iPad longer than 10 s** removes the monitor; it comes back on
  unlock (with the app in front).
- **Disabling the monitor in display settings** pauses odx11 until the iPad
  is unplugged and replugged — otherwise it would immediately come back.
- **Startup takes ~1 s** after the app connects (connector hotplug).
- **The receiver app is a TestFlight beta.** If the beta closes before an App
  Store release, there is no receiver.

## Security

- The daemon **listens on nothing** — no port, no D-Bus. It only dials out to
  the iPad through usbmuxd and reads touches from that one connection.
- The **root helper** can only force one allowlisted connector on/off (and
  write an EDID, which lockdown blocks anyway). Any process running as you can
  invoke it via the sudoers rule — the impact is an extra/missing monitor.
- **The OpenDisplay app has no authentication** (protocol v3). Anyone on the
  iPad's Wi-Fi, and any local user via the world-writable usbmuxd socket, can
  connect to it and *replace* the picture or receive your touches. They cannot
  see your desktop or click on your PC. If that matters, turn off the iPad's
  Wi-Fi while using it.
- Touch is clamped to the virtual monitor; the iPad cannot click on your
  main screen.

## Troubleshooting

| Symptom | Check |
|---|---|
| Nothing happens when the app opens | `journalctl --user -u odx11 -f`. "dial refused" = the app is not in front or the iPad is locked. |
| `no USB iOS device` / not paired | Unlock, tap Trust, `idevicepair pair`, `idevicepair validate`. Charge-only cables don't work. |
| `root helper ... not installed` | `bash install.sh` again. |
| `config connector ... is not the helper's allowlisted` | `[display] connector` must match `/etc/odx11/connector`; re-run `install.sh` or fix the config. |
| Monitor appears but iPad stays black | Make sure the app is in front; check `journalctl` for pipeline errors; `gst-inspect-1.0 vaapih264enc` must succeed. |
| Text too small / too big | `[display] divisor` (lower = smaller text, sharper). |
| Service not started after login (bare WM) | Your WM must run XDG autostart and `systemctl --user import-environment DISPLAY XAUTHORITY`. |
| A stuck invisible monitor after a crash | `sudo -n /usr/local/sbin/odx11-connector detect` (the service does this on every exit). |

## Uninstall

```bash
bash install.sh --uninstall        # service, autostart entry, root helper
```

Your `~/.config/odx11/config.toml` and the desktop's saved monitor layout are left in place.

## Development

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q tests/                 # no iPad, X server or GPU needed
python3 tools/fake_receiver.py --port 9000            # strict stand-in for the iPad...
python3 -m odx11 run --tcp 127.0.0.1:9000             # ...that the daemon dials directly
bash tools/idle_cpu.sh 10                             # AC 23: idle CPU of the running service
d2 docs/architecture.d2 docs/architecture.svg         # render the diagram on demand (not committed)
```

Layout and invariants for contributors: [`CLAUDE.md`](CLAUDE.md). The
protocol this implements: upstream
[`PROTOCOL.md`](https://github.com/peetzweg/opendisplay/blob/main/PROTOCOL.md).

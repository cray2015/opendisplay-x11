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

# venv — only if one is needed; PyGObject comes from apt
python3 -m venv --system-site-packages .venv

# run — foreground session against the attached iPad
python3 -m odx11 run --verbose

# list — attached iOS devices as usbmuxd reports them
python3 -m odx11 list

# service — install/start the user daemon (after helper/install.sh); logs; remove
bash systemd/install.sh && journalctl --user -u odx11 -f
bash systemd/install.sh --uninstall

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
Unit tests cover pure modules only (`protocol`, `annexb`, mode sizing, input
mapping, state machine). Anything touching usbmuxd, xrandr, sysfs or the GPU is
verified by hand against the acceptance criteria in `PROJECT_SPEC.md` §10.

## Layout
- `odx11/protocol.py` is the only module that knows the wire format (framing, demux, message shapes).
- `odx11/annexb.py` owns every NAL-level rule; `pipeline.py` never edits bytes.
- `odx11/display.py` is the only caller of xrandr and of the root helper.
- `odx11/daemon.py` owns every timer and the session state machine.
- `helper/odx11-connector` is the only code that runs as root.

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
- **The root helper writes only the allowlisted connector's sysfs status and debugfs EDID** — it is the project's entire privileged surface. See `PROJECT_SPEC.md` §8.5.
- **Never poll; use usbmuxd `Listen`, RandR and XDamage events, with timers only for ping, grace and redial** — earlier Cinnamon applets were disabled for idle CPU spikes. See `PROJECT_SPEC.md` §4.
- **Treat `Detached` as intent: tear down immediately, no grace** — grace is only for EOF, silence and `sleeping`. See `PROJECT_SPEC.md` §8.4.
- **Every tunable lives in the config file (`odx11/config.py` + `config.example.toml`), never as a module constant** — only values the protocol or hardware fixes stay in code; `tests/test_config.py` keeps the example equal to the defaults. See `PROJECT_SPEC.md` §8.6.
- **Ignore unknown control `type`s and fields, logging each unknown type once** — input arrives at hundreds of messages per second. See `PROJECT_SPEC.md` §7.4.

# opendisplay-x11

Turns an iPad into a wired, extended second monitor for a Linux X11 desktop
(Cinnamon on Mint 22.3), using the unmodified
[OpenDisplay](https://github.com/peetzweg/opendisplay) iPad app as the receiver.
The desktop is extended onto a virtual monitor (a spare HDMI connector forced
on), captured, hardware-encoded to H.264 with VA-API, and sent over the USB
cable through stock `usbmuxd`. Touch and two-finger scroll on the iPad drive
the pointer.

Status: **M2** — a user service that extends the desktop whenever the iPad is
plugged in and the app is open. Design and acceptance criteria: [`PROJECT_SPEC.md`](PROJECT_SPEC.md).

## Setup

1. System packages:
   ```bash
   sudo apt install usbmuxd libimobiledevice-utils \
     python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
     gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
     gstreamer1.0-vaapi python3-xlib python3-pytest vainfo intel-media-va-driver
   ```
2. On the iPad: install OpenDisplay from its
   [TestFlight beta](https://testflight.apple.com/join/3NYaY11c), plug in the
   cable, tap **Trust**, then check `idevicepair validate` succeeds.
3. Install the root helper (the only privileged part: it forces one
   allowlisted connector on/off). Pick a connector with nothing plugged into
   it — `ls /sys/class/drm` lists them:
   ```bash
   sudo bash helper/install.sh card1-HDMI-A-1
   ```
   Remove it again with `sudo bash helper/install.sh --uninstall`.
4. Install the user service (starts now and at every login):
   ```bash
   bash systemd/install.sh            # --uninstall to remove
   journalctl --user -u odx11 -f      # logs
   ```
5. Optional: `cp config.example.toml ~/.config/odx11/config.toml` and edit.
   Every setting is documented there; without a file the defaults apply.

## Usage

```bash
python3 -m odx11 list               # attached iOS devices
python3 -m odx11 run --verbose      # open the app on the iPad first; Ctrl-C to stop
```

With the service installed there is nothing to run: plug in, open the app.
`odx11 run --verbose` is the same daemon in the foreground, for debugging
(stop the service first). The first time, the iPad appears to the right of the
main monitor; arrange it in Cinnamon's Display settings and that is remembered. Tap clicks, drag
drags, two-finger scroll scrolls. Ctrl-C removes the virtual monitor and
Cinnamon moves its windows back.

## Development

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q tests/                 # no iPad, X server or GPU needed
python3 tools/fake_receiver.py --port 9000            # strict stand-in receiver...
python3 -m odx11 run --tcp 127.0.0.1:9000             # ...that this dials directly
d2 docs/architecture.d2 docs/architecture.svg         # render the diagram on demand
```

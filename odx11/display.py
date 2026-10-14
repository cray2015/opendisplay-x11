# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Virtual monitor: mode sizing, the connector force switch, xrandr and output geometry
(PROJECT_SPEC.md §8.1–8.2, §8.7).

The only caller of xrandr and the only writer of the connector's sysfs `status`. Pure sizing/placement
functions sit at the top so tests need no X server.
"""
import logging
import os
import select
import subprocess
import time
from .i18n import _
from typing import NamedTuple

log = logging.getLogger(__name__)

class Geometry(NamedTuple):
    x: int
    y: int
    w: int
    h: int


def floor16(v: float) -> int:
    return int(v) // 16 * 16


def logical_mode(panel_w: int, panel_h: int, divisor: float, override=None):
    """(mode_w, mode_h, rotation) for a panel given in its current orientation.

    The mode is always landscape; portrait is the same mode rotated left (§4)."""
    if override:
        mw, mh = max(override), min(override)
    else:
        mw = floor16(max(panel_w, panel_h) / divisor)
        mh = floor16(min(panel_w, panel_h) / divisor)
    rotation = "normal" if panel_w >= panel_h else "left"
    return mw, mh, rotation


def fit_mode(mw: int, mh: int, rotation: str, max_w=None, max_h=None) -> tuple[int, int]:
    """Shrink a landscape mode, keeping its shape, until the stream fits the receiver's
    decode ceiling. The ceiling is in the current orientation, the mode is landscape."""
    ow, oh = (mw, mh) if rotation == "normal" else (mh, mw)
    scale = min(1.0, (max_w / ow) if max_w else 1.0, (max_h / oh) if max_h else 1.0)
    if scale >= 1.0:
        return mw, mh
    return max(16, floor16(mw * scale)), max(16, floor16(mh * scale))


def placement(main: Geometry, w: int, h: int, side: str, align: str) -> tuple[int, int]:
    """Top-left of a w×h output placed on `side` of `main`, aligned along that edge."""
    def along(main_start, main_len, length):
        if align == "center":
            return main_start + (main_len - length) // 2
        if align == "end":
            return main_start + main_len - length
        return main_start
    if side == "right":
        return main.x + main.w, along(main.y, main.h, h)
    if side == "left":
        return main.x - w, along(main.y, main.h, h)
    if side == "below":
        return along(main.x, main.w, w), main.y + main.h
    if side == "above":
        return along(main.x, main.w, w), main.y - h
    raise ValueError(f"bad side {side!r}")


def xrandr_name(kernel_connector: str) -> str:
    """Kernel 'HDMI-A-1' → modesetting driver's xrandr 'HDMI-1'."""
    return kernel_connector.replace("HDMI-A-", "HDMI-")


def cvt_modeline(w: int, h: int, refresh: int) -> list[str]:
    out = subprocess.run(["cvt", "-r", str(w), str(h), str(refresh)],
                         capture_output=True, text=True, check=True).stdout
    line = next(l for l in out.splitlines() if l.startswith("Modeline"))
    return line.split()[2:]          # drop 'Modeline' and the quoted name


class VirtualDisplay:
    """Forces the allowlisted connector on, sizes and places it, and always undoes it.

    `cfg` is the [display] section of the config."""

    def __init__(self, cfg):
        from Xlib import display as xdisplay
        from Xlib.ext import randr
        self._randr = randr
        self.cfg = cfg
        self.connector = None           # kernel name, resolved at attach ("auto" → a free port)
        self.output = None              # its xrandr name
        self.mode_name = None
        self.forced = False
        self.attached = False
        self.x = xdisplay.Display()
        self.root = self.x.screen().root
        base = self.x.query_extension("RANDR").first_event
        self._randr_events = {base, base + 1}      # RRScreenChangeNotify, RRNotify (crtc/output)
        randr.select_input(self.root, randr.RROutputChangeNotifyMask | randr.RRScreenChangeNotifyMask
                           | randr.RRCrtcChangeNotifyMask)

    # --- RandR queries ---------------------------------------------------------
    def _outputs(self):
        # _current: no connector re-probe. Hotplug of our forced connector is reported by the
        # kernel uevent, which the X server handles itself before RandR notifies us.
        res = self._randr.get_screen_resources_current(self.root)
        for o in res.outputs:
            info = self._randr.get_output_info(self.x, o, res.config_timestamp)
            yield o, info, res

    def _output_info(self, name):
        for o, info, res in self._outputs():
            if info.name == name:
                return o, info, res
        raise RuntimeError(f"xrandr has no output {name}")

    def _crtc_geometry(self, name):
        _, info, res = self._output_info(name)
        if not info.crtc:
            return None
        c = self._randr.get_crtc_info(self.x, info.crtc, res.config_timestamp)
        return Geometry(c.x, c.y, c.width, c.height)

    def main_geometry(self) -> Geometry:
        name = self.cfg.main_output
        if name is None:
            prim = self._randr.get_output_primary(self.root).output
            name = next(info.name for o, info, _ in self._outputs() if o == prim)
        g = self._crtc_geometry(name)
        if g is None:
            raise RuntimeError(f"main output {name} is not enabled")
        return g

    def geometry(self) -> Geometry | None:
        """Current geometry of the virtual output in root coordinates (post-rotation),
        or None if it is not enabled (e.g. turned off in Cinnamon's Display settings)."""
        return self._crtc_geometry(self.output) if self.output else None

    def fileno(self) -> int:
        """The X connection, for the event loop to watch for RandR changes."""
        return self.x.fileno()

    def drain_events(self) -> bool:
        """Consume pending X events; True if any was a RandR change."""
        changed = False
        while self.x.pending_events():
            ev = self.x.next_event()
            changed |= ev.type in self._randr_events
        return changed

    def _connected(self) -> bool:
        return self._output_info(self.output)[1].connection == self._randr.Connected

    def _wait_until(self, predicate, timeout: float) -> bool:
        """Block on RandR events (not polling) until predicate() holds; False on timeout."""
        deadline = time.monotonic() + timeout
        while not predicate():
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            if not self.x.pending_events():
                select.select([self.x.fileno()], [], [], left)
            while self.x.pending_events():
                self.x.next_event()
        return True

    # --- privileged + xrandr ---------------------------------------------------
    def _force(self, state):
        """Write the connector's force switch: no root, the udev rule makes it group-writable."""
        log.debug("connector %s <- %s", self.connector, state)
        write_status(self.connector, state)

    def _xrandr(self, *args, check=True):
        log.debug("xrandr %s", " ".join(args))
        return subprocess.run(["xrandr", *args], check=check, capture_output=not check)

    def _ensure_mode(self, mw: int, mh: int):
        name = f"odx11_{mw}x{mh}"
        if name == self.mode_name:
            return name
        self._xrandr("--newmode", name, *cvt_modeline(mw, mh, self.cfg.refresh_hz), check=False)  # may exist
        self._xrandr("--addmode", self.output, name, check=False)                                # may exist
        self.mode_name = name
        return name

    def attach(self, panel_w: int, panel_h: int, device_cfg, limits=None) -> Geometry:
        """Bring the virtual output up at the logical mode for this panel.

        Placement belongs to Cinnamon (§4): the mode is added before the connector is
        forced, so Cinnamon's hotplug handling can restore the user's saved layout by
        itself. We only fix up what it leaves wrong — the mode/rotation if the iPad's
        orientation differs, or the position if Cinnamon has no saved layout at all."""
        mw, mh, rotation = logical_mode(panel_w, panel_h, self.cfg.divisor, device_cfg.mode)
        if limits is not None:
            fitted = fit_mode(mw, mh, rotation, limits.max_w, limits.max_h)
            if fitted != (mw, mh):
                log.info("%dx%d is above this iPad's decode limit — using %dx%d", mw, mh, *fitted)
                mw, mh = fitted
        ow, oh = (mw, mh) if rotation == "normal" else (mh, mw)
        if not self.forced:
            self.connector = resolve_connector(self.cfg.connector)
            if self.connector is None:
                raise RuntimeError("no free video port")
            self.output = xrandr_name(self.connector)
        previous_mode = self.mode_name
        name = self._ensure_mode(mw, mh)
        if not self.forced:
            self.forced = True          # set first: a failure mid-write must still unforce
            self._force("on")
            if not self._wait_until(self._connected, self.cfg.connect_timeout_s):
                raise RuntimeError(f"{self.output} did not become connected")
            # Give Cinnamon the chance to enable it from its saved layout.
            if self._wait_until(lambda: self.geometry() is not None, self.cfg.settle_timeout_s):
                log.debug("Cinnamon enabled %s at %s", self.output, self.geometry())
        g = self.geometry()
        if g is not None and (g.w, g.h) == (ow, oh):
            how = "as restored by Cinnamon" if not self.attached else "unchanged"
        elif g is not None:
            # Enabled (by Cinnamon, or by us before a rotation) at another size: keep the position.
            self._xrandr("--output", self.output, "--mode", name, "--rotate", rotation)
            how = "resized in place"
        else:
            # No saved layout: first-time default from config.
            px, py = placement(self.main_geometry(), ow, oh, device_cfg.side, device_cfg.align)
            self._xrandr("--output", self.output, "--mode", name, "--rotate", rotation, "--pos", f"{px}x{py}")
            how = f"placed {device_cfg.side} (no saved layout)"
        g = self.geometry()
        if g is None or (g.w, g.h) != (ow, oh):
            raise RuntimeError(f"{self.output} did not take {ow}x{oh} (got {g})")
        if previous_mode and previous_mode != name:
            # Text size changed in place: the old mode is no longer in use — drop it.
            self._xrandr("--delmode", self.output, previous_mode, check=False)
            self._xrandr("--rmmode", previous_mode, check=False)
        self.attached = True
        log.info("virtual monitor %s %dx%d+%d+%d (%s, %s)", self.output, g.w, g.h, g.x, g.y, rotation, how)
        return g

    def teardown(self):
        """Idempotent; safe from atexit and signal paths. Never raises.

        Unforce first: with the connector still forced, Cinnamon re-enables an output we
        switch off (from its saved layout), and the mode is then in use and cannot be
        removed. After `detect` it is a genuine unplug."""
        if not self.forced and not self.mode_name:
            return                      # never touch an output we did not set up
        if self.forced:
            try:
                self._force("detect")
                if not self._wait_until(lambda: not self._connected(), self.cfg.connect_timeout_s):
                    log.warning("%s still connected after detect", self.output)
            except Exception as e:      # keep going: the mode should still be removed
                log.error("teardown: unforcing %s failed: %s", self.connector, e)
        self.forced = False
        self.attached = False
        if self.mode_name:
            for args in (["--output", self.output, "--off"], ["--delmode", self.output, self.mode_name],
                         ["--rmmode", self.mode_name]):
                r = self._xrandr(*args, check=False)
                if r.returncode != 0 and args[0] != "--off":
                    log.warning("teardown: xrandr %s failed: %s", " ".join(args),
                                r.stderr.decode(errors="replace").strip())
            self.mode_name = None
        log.info("virtual monitor removed")

SYSFS_DRM = "/sys/class/drm"
UDEV_GROUP = "plugdev"                 # the group the packaged udev rule grants (§8.7)


def status_path(connector: str, root: str = SYSFS_DRM) -> str | None:
    """sysfs force switch for a kernel connector name (e.g. HDMI-A-1), on whichever card has it."""
    try:
        for entry in sorted(os.listdir(root)):
            card, _, name = entry.partition("-")
            if name == connector and card.startswith("card") and card[4:].isdigit():
                return os.path.join(root, entry, "status")
    except OSError:
        pass
    return None


def write_status(connector: str, state: str, root: str = SYSFS_DRM):
    if state not in ("on", "detect"):
        raise ValueError(state)
    path = status_path(connector, root)
    if path is None:
        raise FileNotFoundError(f"no connector {connector} under {root}")
    with open(path, "w") as f:
        f.write(state)


def connector_problem(cfg, root: str = SYSFS_DRM) -> str | None:
    """Why the daemon cannot force `cfg.connector` ([display] section), in words a user can act
    on — or None when it can."""
    connector = resolve_connector(cfg.connector, root)
    if connector is None:
        return _("This computer has no free video port. Every HDMI/DisplayPort socket has a monitor "
                 "in it; free one up, or see why one is needed in the README.")
    path = status_path(connector, root)
    if path is None:
        return _("There is no {port} video port on this computer. Choose a free one in the "
                 "settings, or set it back to Automatic.").format(port=connector)
    try:
        with open(path) as f:
            status = f.read().strip()
    except OSError as e:
        return f"Cannot read {path}: {e}"
    if not os.access(path, os.W_OK):
        return _("Setup is not finished: this account may not switch {port}. It needs to be "
                 "in the '{group}' group and the opendisplay-x11 package installed (its udev rule "
                 "grants the permission); log out and back in after joining the group.").format(
                     port=connector, group=UDEV_GROUP)
    if status == "connected" and not _forced_by_us(path):
        return _("{port} has a real monitor plugged in. Choose a free video port in the settings, "
                 "or set it back to Automatic.").format(port=connector)
    return None


def hdmi_ports(root: str = SYSFS_DRM) -> list[tuple[str, str]]:
    """[(kernel name, "free" | "ours" | "monitor")] for every HDMI-A connector, in order.
    "ours" = connected with no EDID: forced by us (now, or left behind by a crash)."""
    ports = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return ports
    for entry in entries:
        card, _, name = entry.partition("-")
        if not (card.startswith("card") and card[4:].isdigit() and name.startswith("HDMI-A-")):
            continue
        path = os.path.join(root, entry, "status")
        try:
            with open(path) as f:
                status = f.read().strip()
        except OSError:
            continue
        if status == "connected":
            ports.append((name, "ours" if _forced_by_us(path) else "monitor"))
        else:
            ports.append((name, "free"))
    return ports


def resolve_connector(setting: str, root: str = SYSFS_DRM) -> str | None:
    """The kernel connector to use. A fixed name is used as given; "auto" prefers a port we
    already forced (so a restart keeps the same monitor), then the first free one."""
    if setting != "auto":
        return setting
    ports = hdmi_ports(root)
    for wanted in ("ours", "free"):
        for name, state in ports:
            if state == wanted:
                return name
    return None


def _forced_by_us(path: str) -> bool:
    """A forced connector reads 'connected' too; the kernel exposes no 'forced' flag, but a forced
    port with no sink has no EDID. Treat connected-without-EDID as ours (left from a crash)."""
    edid = os.path.join(os.path.dirname(path), "edid")
    try:
        with open(edid, "rb") as f:
            return not f.read(1)
    except OSError:
        return True


def release_forced(root: str = SYSFS_DRM) -> list[str]:
    """Unforce every HDMI connector that is connected with no EDID — i.e. forced by us, left
    behind by a crash or SIGKILL. Independent of the config on purpose: systemd's ExecStopPost
    must work even if the config is broken or changed mid-session. A real monitor always has
    an EDID, so it is never touched; `detect` is harmless anyway (back to hotplug detection)."""
    released = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return released
    for entry in entries:
        card, _, name = entry.partition("-")
        if not (card.startswith("card") and name.startswith("HDMI-A-")):
            continue
        path = os.path.join(root, entry, "status")
        try:
            with open(path) as f:
                connected = f.read().strip() == "connected"
            if connected and _forced_by_us(path):
                with open(path, "w") as f:
                    f.write("detect")
                released.append(name)
        except OSError as e:
            log.warning("could not release %s: %s", name, e)
    return released

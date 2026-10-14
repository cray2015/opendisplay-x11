# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""The setup checklist (PROJECT_SPEC.md §8.8). Each check answers one question a
user can act on; slow probes (encoder, pairing) are separate so the window can
run them in the background. No GTK here."""
import os
import shutil
import subprocess
from dataclasses import dataclass

from . import display
from .i18n import _

TESTFLIGHT = "https://testflight.apple.com/join/3NYaY11c"
README = "https://github.com/cray2015/opendisplay-x11"
WHY_PORT = README + "#why-a-free-video-port-is-required"


@dataclass
class Check:
    key: str
    title: str
    ok: bool | None             # True ✓, False ✗, None unknown / not checkable
    detail: str = ""
    link: str | None = None


def session(env=os.environ) -> Check:
    ok = env.get("XDG_SESSION_TYPE") == "x11" and bool(env.get("DISPLAY"))
    return Check("session", _("Desktop session is X11"), ok,
                 "" if ok else _("OpenDisplay for X11 needs an X11 session; Wayland is not supported."))


def video_port(setting: str, root: str = display.SYSFS_DRM) -> Check:
    port = display.resolve_connector(setting, root)
    if port is None:
        return Check("port", _("Free video port"), False,
                     _("Every HDMI/DisplayPort socket has a monitor in it. Free one up."), WHY_PORT)
    states = dict(display.hdmi_ports(root))
    if states.get(port) == "monitor":
        return Check("port", _("Free video port"), False,
                     _("{port} has a monitor plugged in. Choose another port, or Automatic.").format(port=port))
    if port not in states:
        return Check("port", _("Free video port"), False, _("There is no {port} on this computer.").format(port=port))
    return Check("port", _("Free video port"), True, _("Using {port}.").format(port=port))


def permission(setting: str, root: str = display.SYSFS_DRM, groups=None) -> Check:
    port = display.resolve_connector(setting, root)
    path = display.status_path(port, root) if port else None
    if path is None:
        return Check("permission", _("Permission to use the port"), None, _("Needs a free port first."))
    if os.access(path, os.W_OK):
        return Check("permission", _("Permission to use the port"), True)
    groups = groups if groups is not None else _groups()
    if display.UDEV_GROUP not in groups:
        detail = _("This account is not in the '{group}' group. Add it in Users and Groups, "
                   "then log out and back in.").format(group=display.UDEV_GROUP)
    else:
        detail = _("The package's permission rule is not active yet. Restart the computer.")
    return Check("permission", _("Permission to use the port"), False, detail)


def _groups() -> set[str]:
    import grp
    return {grp.getgrgid(g).gr_name for g in os.getgroups()}


def encoder(timeout=10) -> Check:
    """Slow: asks GStreamer (may rescan plugins)."""
    try:
        r = subprocess.run(["gst-inspect-1.0", "vaapih264enc"], capture_output=True, timeout=timeout)
        ok = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    return Check("encoder", _("Video encoder"), ok,
                 "" if ok else _("No VA-API H.264 encoder: the graphics driver's video support is missing."))


def ipad(timeout=10) -> Check:
    """Slow: talks to usbmuxd and the iPad."""
    if not shutil.which("idevice_id"):
        return Check("ipad", _("iPad paired with this computer"), None, _("iPad tools are not installed."))
    try:
        ids = subprocess.run(["idevice_id", "-l"], capture_output=True, text=True, timeout=timeout).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        ids = []
    if not ids:
        return Check("ipad", _("iPad paired with this computer"), None, _("Plug in the iPad to check."))
    try:
        ok = subprocess.run(["idevicepair", "validate"], capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    return Check("ipad", _("iPad paired with this computer"), ok,
                 "" if ok else _("Unlock the iPad and press Pair, then tap Trust on the iPad."))


def app(facts: dict) -> Check:
    """The OpenDisplay app can't be seen from this side; it is proven once it has connected."""
    seen = facts.get("state") in ("streaming", "grace")
    return Check("app", _("OpenDisplay app on the iPad"), True if seen else None,
                 "" if seen else _("Scan the code with the iPad's camera to get it (TestFlight)."), TESTFLIGHT)


def pair(timeout=60) -> tuple[bool, str]:
    """Ask the iPad to trust this computer. Returns (ok, message)."""
    try:
        r = subprocess.run(["idevicepair", "pair"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, _("Pairing did not finish: {error}").format(error=e)
    text = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        return True, _("Paired.")
    if "Please accept the trust dialog" in text or "trust" in text.lower():
        return False, _("Tap Trust on the iPad, then press Pair again.")
    if "passcode" in text.lower() or "locked" in text.lower():
        return False, _("Unlock the iPad, then press Pair again.")
    return False, text or _("Pairing failed.")

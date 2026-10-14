"""Desktop notifications from the daemon (PROJECT_SPEC.md §8.8).

Sent through the freedesktop notification service on the session bus — an
outgoing call to whatever notification daemon the desktop runs; nothing new
listens. `decide()` is pure: which notification, if any, a state change earns.
Clicking a notification opens the settings window.
"""
import logging
import subprocess

from . import state
from .i18n import _

log = logging.getLogger(__name__)

APP_NAME = "OpenDisplay for X11"
ICON = "io.github.cray2015.OpenDisplayX11"       # app ID: desktop file and icon share it
NEEDS_ACTION_AFTER = 2       # refused dials before "open the app" — not on the very first try


def decide(prev: dict, cur: dict, cfg) -> list[tuple[str, str, str]]:
    """[(kind, summary, body)] for the transition prev → cur. `cfg` is [notifications]."""
    if not cfg.enabled:
        return []
    p, c = prev.get("state", state.OFF), cur.get("state", state.OFF)
    out = []
    if c == state.STREAMING and p != state.STREAMING and cfg.connected:
        if p == state.GRACE:
            out.append(("connected", _("iPad reconnected"), _("Your desktop is back on the iPad.")))
        else:
            out.append(("connected", _("iPad connected — desktop extended"), ""))
    if (c == state.DIALING and cfg.needs_action
            and cur.get("refused", 0) >= NEEDS_ACTION_AFTER > prev.get("refused", 0)):
        out.append(("needs_action", _("Open OpenDisplay on your iPad"),
                    _("It's plugged in, but the app isn't open or the iPad is locked.")))
    lost = p in (state.STREAMING, state.GRACE) and c in (state.IDLE, state.DIALING, state.OFF)
    if lost and cfg.disconnected:
        out.append(("disconnected", _("iPad disconnected"), _("The extra screen was removed.")))
    if c == state.PROBLEM and cur.get("problem") != prev.get("problem") and cfg.problems:
        out.append(("problem", _("Setup needed"), cur.get("problem") or ""))
    return out


class Notifier:
    """Sends notifications over D-Bus; failures (no notification daemon) are logged once."""

    def __init__(self):
        self._bus = None
        self._warned = False
        self._ids = set()
        try:
            from gi.repository import Gio
            self._Gio = Gio
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._bus.signal_subscribe(
                "org.freedesktop.Notifications", "org.freedesktop.Notifications", "ActionInvoked",
                "/org/freedesktop/Notifications", None, Gio.DBusSignalFlags.NONE, self._on_action)
        except Exception as e:                  # no session bus: notifications just don't happen
            log.info("desktop notifications unavailable: %s", e)
            self._bus = None

    def send(self, kind: str, summary: str, body: str):
        if self._bus is None:
            return
        from gi.repository import GLib
        urgency = GLib.Variant("y", 2 if kind == "problem" else 1)
        params = GLib.Variant("(susssasa{sv}i)", (
            APP_NAME, 0, ICON, summary, body, ["default", _("Open settings")],
            {"urgency": urgency, "desktop-entry": GLib.Variant("s", ICON)}, -1))
        try:
            reply = self._bus.call_sync(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify", params, GLib.VariantType("(u)"),
                self._Gio.DBusCallFlags.NONE, 2000, None)
            self._ids.add(reply.unpack()[0])
        except Exception as e:
            if not self._warned:
                self._warned = True
                log.info("could not show a notification (is a notification daemon running?): %s", e)

    def _on_action(self, conn, sender, path, iface, signal, params):
        nid, action = params.unpack()
        if nid in self._ids and action == "default":
            try:
                subprocess.Popen(["odx11", "settings"], start_new_session=True)
            except OSError as e:
                log.warning("could not open the settings window: %s", e)

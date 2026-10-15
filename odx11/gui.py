# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""The OpenDisplay for X11 settings window — GTK 4 + libadwaita (PROJECT_SPEC.md §8.8).

A separate process (`odx11 settings`); the daemon never depends on it. It
reads the daemon's state file and acts only through what already exists:
systemd (on/off), the config file (settings, applied live by the daemon),
`odx11 release` and `idevicepair`. Floor: GTK 4.14 / libadwaita 1.5.
"""
import logging
import os
import platform
import subprocess
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__, checks, config, display, settings, state  # noqa: E402
from .i18n import _, ngettext  # noqa: E402

log = logging.getLogger(__name__)

APP_ID = "io.github.cray2015.OpenDisplayX11"
ICON_NAME = APP_ID              # the installed icon; also used by source-tree (.Devel) runs
# A source-tree run gets the conventional ".Devel" ID, so it never merges into an installed
# window (GApplication single instance) and can run side by side with it.
if not os.path.abspath(__file__).startswith("/usr/"):
    APP_ID += ".Devel"
SAVE_DELAY_S = 0.8            # sliders: write ~1 s after the last movement
# Standard symbolic icons + libadwaita style classes for each severity (GNOME HIG).
STATUS_ICON = {state.OK: ("emblem-ok-symbolic", "success"),
               state.WAITING: ("emblem-synchronizing-symbolic", "dim-label"),
               state.ATTENTION: ("dialog-warning-symbolic", "warning"),
               state.ERROR: ("dialog-error-symbolic", "error"),
               state.NEUTRAL: ("system-shutdown-symbolic", "dim-label")}


def installed_version() -> str:
    """The package version when installed (what the user upgraded to), else the source's."""
    from . import upgrade
    return upgrade.read_version() or __version__


def _service(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args, "odx11.service"], capture_output=True, text=True)


def _in_background(work, done):
    """Run work() in a thread, then done(result) on the main loop — for slow probes."""
    def run():
        result = work()
        GLib.idle_add(lambda: done(result) and False)
    threading.Thread(target=run, daemon=True).start()


class Window(Adw.ApplicationWindow):
    def __init__(self, app, config_path: str, config_error: str | None):
        super().__init__(application=app, title=_("OpenDisplay for X11"))
        self.set_default_size(600, 820)
        self.config_path = config_path
        self.cfg = config.parse({})
        self._updating = False            # set while widgets are filled from the config/state
        self._save_timer = None
        self._our_write = 0.0
        self.facts = {"state": state.OFF}
        self._grace_timer = None
        self._checks = {}

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append(_("About OpenDisplay for X11"), "app.about")
        menu.append(_("Quit"), "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       primary=True, tooltip_text=_("Main Menu")))
        view.add_top_bar(header)
        self.banner = Adw.Banner()
        view.add_top_bar(self.banner)
        page = Adw.PreferencesPage()
        page.add(self._build_status())
        page.add(self._build_settings())
        page.add(self._build_help())
        page.add(self._build_remove())
        view.set_content(page)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        self._load_config(config_error)
        self._watch(state.path(), self._on_state_changed)
        self._watch(config_path, self._on_config_changed)
        self._on_state_changed()
        self._refresh_checks()

    # --- building ----------------------------------------------------------------------
    def _build_status(self):
        group = Adw.PreferencesGroup()
        self.status_row = Adw.ActionRow(title_lines=2, subtitle_lines=3)
        self.status_icon = Gtk.Image(pixel_size=32, valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.status_row.add_prefix(self.status_icon)
        self.power = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text=_("Turn OpenDisplay for X11 on or off"))
        self.power.update_property([Gtk.AccessibleProperty.LABEL], [_("OpenDisplay for X11 on or off")])
        self.power.connect("notify::active", self._on_power)
        self.status_row.add_suffix(self.power)
        group.add(self.status_row)
        return group

    def _scale_row(self, title, subtitle, left, right, count):
        row = Adw.ActionRow(title=title, subtitle=subtitle or None)
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        box.append(Gtk.Label(label=left, css_classes=["dim-label", "caption"]))
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, count - 1, 1)
        scale.set_size_request(180, -1)
        scale.set_draw_value(False)
        scale.set_round_digits(0)
        for i in range(count):
            scale.add_mark(i, Gtk.PositionType.BOTTOM, None)
        scale.update_property([Gtk.AccessibleProperty.LABEL], [title])
        scale.connect("value-changed", self._on_setting)
        box.append(scale)
        box.append(Gtk.Label(label=right, css_classes=["dim-label", "caption"]))
        row.add_suffix(box)
        return row, scale

    def _combo_row(self, title, labels, subtitle=None):
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new(labels))
        row.connect("notify::selected", self._on_setting)
        return row

    def _build_settings(self):
        group = Adw.PreferencesGroup(title=_("Settings"),
                                     description=_("Changes apply on their own."))
        self.login_row = Adw.SwitchRow(title=_("Start at login"), subtitle=_("Starts with your desktop"))
        self.login_row.connect("notify::active", self._on_setting)
        group.add(self.login_row)
        self.text_row, self.text_scale = self._scale_row(
            _("Text size"), "", _("smaller"), _("larger"), len(settings.TEXT_SIZES))
        group.add(self.text_row)
        self.perf_row = self._combo_row(_("Performance"), [l for _k, l, _d in settings.PRESET_CHOICES])
        group.add(self.perf_row)
        self.sharp_row, self.sharp_scale = self._scale_row(
            _("Sharpness"), None, _("softer"), _("sharper"), len(settings.SHARPNESS_QP))
        group.add(self.sharp_row)
        self.fps_row = self._combo_row(_("Frame rate"), [l for _v, l in settings.FRAME_RATES])
        group.add(self.fps_row)
        self.idle_row = self._combo_row(_("Pause when nothing changes after"), [l for _v, l in settings.IDLE_PAUSES],
                                        _("Saves power while the iPad's screen is still"))
        group.add(self.idle_row)
        self.convert_row = self._combo_row(_("Colour conversion"), [l for _v, l in settings.CONVERTS],
                                           _("The graphics chip uses far less CPU; Automatic uses it "
                                             "when the video driver allows"))
        group.add(self.convert_row)
        self.press_row = self._combo_row(_("Right-click sensitivity"), [l for _v, l in settings.LONG_PRESSES],
                                         _("How long to hold a finger still for a right-click"))
        group.add(self.press_row)
        self.grace_row = self._combo_row(_("Screen timeout"), [l for _v, l in settings.GRACES],
                                         _("How long the screen stays after a disconnect, so switching apps "
                                           "on the iPad doesn't move your windows"))
        group.add(self.grace_row)
        self.port_row = self._combo_row(_("Video port"), [_("Automatic")], _("The iPad will blink"))
        self._port_values = ["auto"]
        group.add(self.port_row)
        self.notify_row = Adw.SwitchRow(title=_("Desktop notifications"),
                                        subtitle=_("Only when you need to do something"))
        self.notify_row.connect("notify::active", self._on_setting)
        group.add(self.notify_row)
        return group

    def _build_checklist(self):
        self.check_exp = Adw.ExpanderRow(title=_("Setup checklist"))
        self.check_rows = {}
        for key, title in (("session", _("Desktop session is X11")), ("port", _("Free video port")),
                           ("permission", _("Permission to use the port")), ("encoder", _("Video encoder")),
                           ("ipad", _("iPad paired with this computer")), ("app", _("OpenDisplay app on the iPad"))):
            row = Adw.ActionRow(title=title)
            icon = Gtk.Image.new_from_icon_name("content-loading-symbolic")
            row.add_prefix(icon)
            self.check_rows[key] = (row, icon)
            self.check_exp.add_row(row)
        pair = Gtk.Button(label=_("Pair"), valign=Gtk.Align.CENTER)
        pair.connect("clicked", self._on_pair)
        self.check_rows["ipad"][0].add_suffix(pair)
        self.qr = Gtk.DrawingArea(content_width=120, content_height=120, halign=Gtk.Align.CENTER,
                                  margin_top=6, margin_bottom=6)
        self.qr.set_draw_func(self._draw_qr)
        self.qr.update_property([Gtk.AccessibleProperty.LABEL],
                                [_("QR code linking to the OpenDisplay app on TestFlight")])
        qr_row = Adw.ActionRow(title=_("Get the OpenDisplay app"),
                               subtitle=_("Scan with the iPad's camera, or open the link on the iPad"))
        qr_row.add_suffix(self.qr)
        link = Gtk.LinkButton(uri=checks.TESTFLIGHT, label=_("TestFlight link"), valign=Gtk.Align.CENTER)
        qr_row.add_suffix(link)
        self.check_exp.add_row(qr_row)
        return self.check_exp

    def _build_help(self):
        group = Adw.PreferencesGroup(title=_("Help"))
        group.add(self._build_checklist())
        report = Adw.ActionRow(title=_("Copy diagnostic report"),
                               subtitle=_("Versions, checklist and recent log, for a bug report"), activatable=True)
        report.add_suffix(Gtk.Image.new_from_icon_name("edit-copy-symbolic"))
        report.connect("activated", self._on_report)
        group.add(report)
        readme = Adw.ActionRow(title=_("How it works and known limitations"), activatable=True)
        readme.add_suffix(Gtk.Image.new_from_icon_name("adw-external-link-symbolic"))
        readme.connect("activated", lambda *_: self._open_uri(checks.README))
        group.add(readme)
        version = Adw.ActionRow(title=_("Version"))
        version.add_suffix(Gtk.Label(label=installed_version(), css_classes=["dim-label"], selectable=True))
        group.add(version)
        return group

    def _build_remove(self):
        """Destructive, so last and red (GNOME HIG: destructive-action). No confirmation: it is
        undone by turning OpenDisplay for X11 back on."""
        group = Adw.PreferencesGroup(
            description=_("Turns OpenDisplay for X11 off and releases the video port."))
        button = Gtk.Button(label=_("Remove the virtual monitor now"), hexpand=True)
        button.add_css_class("destructive-action")
        button.add_css_class("pill")
        button.connect("clicked", self._on_remove)
        group.add(button)
        return group

    # --- state --------------------------------------------------------------------------
    def _watch(self, path, callback):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        mon.connect("changed", lambda *_: callback())
        setattr(self, f"_mon_{abs(hash(path))}", mon)       # keep a reference

    def _on_state_changed(self):
        self.facts = state.read()
        head, action, level = state.describe(self.facts)
        self.status_row.set_title(GLib.markup_escape_text(head))
        self.status_row.set_subtitle(GLib.markup_escape_text(action))
        icon, cls = STATUS_ICON.get(level, STATUS_ICON[state.NEUTRAL])
        self.status_icon.set_from_icon_name(icon)
        for c in {c for _i, c in STATUS_ICON.values()}:
            self.status_icon.remove_css_class(c)
        self.status_icon.add_css_class(cls)
        running = self.facts["state"] not in (state.OFF, state.PROBLEM)
        if not running and self.facts["state"] == state.OFF:
            running = _service("is-active").stdout.strip() == "active"   # an older daemon writes no file
            if running:
                self.status_row.set_title(GLib.markup_escape_text(_("On")))
                self.status_row.set_subtitle(GLib.markup_escape_text(_("Running — details appear once it publishes its state.")))
        self._updating = True
        self.power.set_active(running)
        self._updating = False
        if self.facts.get("panel"):
            self._update_resolution()
        self.check_rows["app"][0].set_subtitle(checks.app(self.facts).detail)
        self._set_check(checks.app(self.facts))
        # The grace countdown is the one thing that changes without an event: tick while shown.
        if self.facts["state"] == state.GRACE and self._grace_timer is None:
            self._grace_timer = GLib.timeout_add_seconds(1, self._tick_grace)
        return False

    def _tick_grace(self):
        if self.facts.get("state") != state.GRACE:
            self._grace_timer = None
            return GLib.SOURCE_REMOVE
        head, _, _ = state.describe(self.facts)
        self.status_row.set_title(GLib.markup_escape_text(head))
        return GLib.SOURCE_CONTINUE

    # --- config ---------------------------------------------------------------------------
    def _load_config(self, error=None):
        if error is None:
            try:
                self.cfg = config.load(self.config_path)
            except (ValueError, TypeError, OSError) as e:
                error = str(e)
        self.banner.set_title(_("The settings file has an error: {error}").format(error=error) if error else "")
        self.banner.set_revealed(bool(error))
        c = self.cfg
        self._updating = True
        self.login_row.set_active(c.service.enabled)
        self.text_scale.set_value(settings.nearest(settings.TEXT_SIZES, c.display.divisor))
        keys = [k for k, _l, _d in settings.PRESET_CHOICES]
        self.perf_row.set_selected(keys.index(c.video.preset) if c.video.preset in keys else 1)
        self._show_preset()
        convert_keys = [k for k, _l in settings.CONVERTS]
        self.convert_row.set_selected(convert_keys.index(c.video.convert) if c.video.convert in convert_keys else 0)
        self.press_row.set_selected(settings.nearest(settings.LONG_PRESSES, c.input.long_press_ms))
        self.grace_row.set_selected(settings.nearest(settings.GRACES, c.session.grace_s))
        self.notify_row.set_active(c.notifications.enabled)
        self._fill_ports()
        self._updating = False
        self._update_resolution()

    def _fill_ports(self):
        choices = settings.port_choices(display.hdmi_ports())
        self._port_values = [v for v, _ in choices]
        self.port_row.set_model(Gtk.StringList.new([l for _, l in choices]))
        want = self.cfg.display.connector
        self.port_row.set_selected(self._port_values.index(want) if want in self._port_values else 0)

    def _update_resolution(self):
        panel = self.facts.get("panel") or settings.DEFAULT_PANEL
        divisor = settings.TEXT_SIZES[int(self.text_scale.get_value())]
        w, h = settings.resolution(panel, divisor)
        self.text_row.set_subtitle(_("{w} × {h} · the iPad will blink").format(w=w, h=h))

    def _on_config_changed(self):
        if time.monotonic() - self._our_write < 2:          # our own save echoing back
            return
        self._load_config()

    def _show_preset(self):
        """A preset shows its values greyed out; Custom shows and unlocks the user's own."""
        v = self.cfg.video
        custom = v.preset == config.CUSTOM
        was, self._updating = self._updating, True
        self.sharp_scale.set_value(settings.nearest(settings.SHARPNESS_QP, v.effective("qp")))
        self.fps_row.set_selected(settings.nearest(settings.FRAME_RATES, v.effective("fps")))
        self.idle_row.set_selected(settings.nearest(settings.IDLE_PAUSES, v.effective("idle_pause_s")))
        self._updating = was
        for row in (self.sharp_row, self.fps_row, self.idle_row):
            row.set_sensitive(custom)
        desc = {k: d for k, _l, d in settings.PRESET_CHOICES}
        self.perf_row.set_subtitle(desc.get(v.preset, ""))

    def _on_setting(self, *_):
        if self._updating:
            return
        self._update_resolution()
        c = self.cfg
        preset = settings.PRESET_CHOICES[self.perf_row.get_selected()][0]
        if preset != c.video.preset:
            c.video.preset = preset
            self._show_preset()                  # fills the rows from the preset (or Custom values)
        c.service.enabled = self.login_row.get_active()
        c.display.divisor = settings.TEXT_SIZES[int(self.text_scale.get_value())]
        if c.video.preset == config.CUSTOM:      # the rows hold the user's own values
            c.video.qp = settings.SHARPNESS_QP[int(self.sharp_scale.get_value())]
            c.video.fps = settings.FRAME_RATES[self.fps_row.get_selected()][0]
            c.video.idle_pause_s = settings.IDLE_PAUSES[self.idle_row.get_selected()][0]
        c.video.convert = settings.CONVERTS[self.convert_row.get_selected()][0]
        c.input.long_press_ms = settings.LONG_PRESSES[self.press_row.get_selected()][0]
        c.session.grace_s = settings.GRACES[self.grace_row.get_selected()][0]
        c.notifications.enabled = self.notify_row.get_active()
        port = self._port_values[self.port_row.get_selected()] if self._port_values else "auto"
        if port != "auto" and dict(display.hdmi_ports()).get(port) == "monitor":
            self.toasts.add_toast(Adw.Toast.new(_("{port} has a monitor plugged in — choose a free port").format(port=port)))
            self._updating = True
            self.port_row.set_selected(self._port_values.index(c.display.connector)
                                       if c.display.connector in self._port_values else 0)
            self._updating = False
            return
        c.display.connector = port
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(int(SAVE_DELAY_S * 1000), self._save)

    def _save(self):
        self._save_timer = None
        try:
            self._our_write = time.monotonic()
            settings.save(self.cfg, self.config_path)
            self.toasts.add_toast(Adw.Toast(title=_("Saved"), timeout=1))
        except (OSError, ValueError, TypeError) as e:
            self.toasts.add_toast(Adw.Toast.new(_("Could not save: {error}").format(error=e)))
        self._refresh_checks()
        return GLib.SOURCE_REMOVE

    # --- actions ---------------------------------------------------------------------------
    def _on_power(self, *_):
        if self._updating:
            return
        on = self.power.get_active()

        def done(r):
            if r.returncode != 0:
                msg = _("Could not turn on: {error}") if on else _("Could not turn off: {error}")
                self.toasts.add_toast(Adw.Toast.new(msg.format(error=r.stderr.strip())))
            self._on_state_changed()
        def work():
            if on:   # a package upgrade may have replaced the unit file: re-read it first
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            return _service("start" if on else "stop")
        _in_background(work, done)

    def _on_remove(self, *_):
        def work():
            _service("stop")
            return subprocess.run(["odx11", "release"], capture_output=True, text=True)

        def done(r):
            self.toasts.add_toast(Adw.Toast.new(_("Virtual monitor removed — OpenDisplay for X11 is off")))
            self._on_state_changed()
        _in_background(work, done)

    def _on_pair(self, button):
        button.set_sensitive(False)
        self.toasts.add_toast(Adw.Toast.new(_("Look at the iPad: tap Trust if it asks")))

        def done(result):
            ok, msg = result
            button.set_sensitive(True)
            self.toasts.add_toast(Adw.Toast.new(msg))
            self._refresh_checks()
        _in_background(checks.pair, done)

    def _set_check(self, c):
        row, icon = self.check_rows[c.key]
        icon.set_from_icon_name({True: "emblem-ok-symbolic", False: "dialog-error-symbolic",
                                 None: "dialog-question-symbolic"}[c.ok])
        for cls in ("success", "error", "dim-label"):
            icon.remove_css_class(cls)
        icon.add_css_class({True: "success", False: "error", None: "dim-label"}[c.ok])
        row.set_subtitle(GLib.markup_escape_text(c.detail))
        self._checks[c.key] = c
        bad = sum(1 for x in self._checks.values() if x.ok is False)
        self.check_exp.set_subtitle(_("✓ all good") if bad == 0 else
                                    ngettext("{n} thing to fix", "{n} things to fix", bad).format(n=bad))
        if bad:
            self.check_exp.set_expanded(True)

    def _refresh_checks(self):
        setting = self.cfg.display.connector
        for c in (checks.session(), checks.video_port(setting), checks.permission(setting), checks.app(self.facts)):
            self._set_check(c)
        _in_background(checks.encoder, self._set_check)
        _in_background(checks.ipad, self._set_check)

    def _draw_qr(self, area, cr, width, height):
        try:
            import qrcode
        except ImportError:
            return
        qr = qrcode.QRCode(border=2)
        qr.add_data(checks.TESTFLIGHT)
        qr.make(fit=True)
        m = qr.get_matrix()
        n = len(m)
        cell = min(width, height) / n
        cr.set_source_rgb(1, 1, 1)                     # always dark-on-light: cameras need contrast
        cr.rectangle(0, 0, n * cell, n * cell)
        cr.fill()
        cr.set_source_rgb(0, 0, 0)
        for y, line in enumerate(m):
            for x, dark in enumerate(line):
                if dark:
                    cr.rectangle(x * cell, y * cell, cell + 0.5, cell + 0.5)
        cr.fill()

    def _open_uri(self, uri):
        Gtk.UriLauncher.new(uri).launch(self, None, None, None)

    def _on_report(self, *_):
        def work():
            return diagnostic_report(self.cfg, self.config_path, list(getattr(self, "_checks", {}).values()))

        def done(text):
            self.get_clipboard().set_content(
                Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", GLib.Bytes.new(text.encode())))
            self.toasts.add_toast(Adw.Toast.new(_("Diagnostic report copied — paste it into your bug report")))
        _in_background(work, done)


def diagnostic_report(cfg, config_path, check_list) -> str:
    def run(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            return f"({e})"
    try:
        with open("/etc/os-release") as f:
            distro = next((l.split("=", 1)[1].strip().strip('"') for l in f if l.startswith("PRETTY_NAME=")), "?")
    except OSError:
        distro = "?"
    lines = [
        "OpenDisplay for X11 — diagnostic report",
        f"version: {__version__}",
        f"system: {distro}; kernel {platform.release()}; session {os.environ.get('XDG_SESSION_TYPE')}",
        f"desktop: {os.environ.get('XDG_CURRENT_DESKTOP')}",
        f"ports: {display.hdmi_ports()}",
        f"state: {state.read()}",
        "checklist:",
        *[f"  {'✓' if c.ok else ('✗' if c.ok is False else '?')} {c.title} {c.detail}" for c in check_list],
        f"settings ({config_path}):",
        *("  " + l for l in settings.to_toml(cfg).splitlines() if l and not l.startswith("#")),
        "recent log:",
        run("journalctl", "--user", "-u", "odx11", "-n", "200", "--no-pager", "-o", "short-iso"),
    ]
    return "\n".join(lines) + "\n"


class App(Adw.Application):
    def __init__(self, config_path, config_error):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.config_path, self.config_error = config_path, config_error

    def do_startup(self):
        Adw.Application.do_startup(self)
        for name, handler, accels in (("about", self._about, []), ("quit", lambda *_a: self.quit(), ["<Control>q"])):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        self.set_accels_for_action("window.close", ["<Control>w"])

    def do_activate(self):
        win = self.props.active_window or Window(self, self.config_path, self.config_error)
        win.present()

    def _about(self, *_a):
        about = Adw.AboutDialog(
            application_name=_("OpenDisplay for X11"), application_icon=ICON_NAME, version=installed_version(),
            developer_name="Vibhanshu Biswas", website=checks.README, issue_url=checks.README + "/issues",
            copyright="© 2026 Vibhanshu Biswas", license_type=Gtk.License.GPL_3_0,
            comments=_("Use an iPad as a second monitor over USB.\n\n"
                       "An unofficial sender for the OpenDisplay iPad app — not affiliated with the "
                       "OpenDisplay project."))
        about.add_link(_("OpenDisplay project"), "https://github.com/peetzweg/opendisplay")
        about.present(self.props.active_window)


def main(config_path: str, config_error: str | None = None) -> int:
    return App(config_path, config_error).run([])

# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Restart onto a new version after a package upgrade (PROJECT_SPEC.md §8.7).

The package ships /usr/share/opendisplay-x11/VERSION; every upgrade replaces it.
The installed daemon watches that file (inotify — no polling). When it changes,
the daemon waits for dpkg to report the package fully installed, then exits
with EX_RESTART; the unit's RestartForceExitStatus brings it straight back on
the new code. Updates from any update manager therefore apply silently, with a
brief cut-off of the iPad session. Developer runs from a source tree never do this.
"""
import hashlib
import logging
import os
import subprocess

log = logging.getLogger(__name__)

VERSION_FILE = "/usr/share/opendisplay-x11/VERSION"
SERVICE_FILE = "/usr/share/opendisplay-x11/SERVICE"   # fingerprint of what the daemon runs
# Modules only the settings window imports. Changes to these never restart the service;
# tests/test_daemon.py fails the build if a daemon-side module ever imports one of them.
GUI_ONLY = {"gui.py", "settings.py", "checks.py"}
PACKAGE = "opendisplay-x11"
EX_RESTART = 75             # sysexits EX_TEMPFAIL; packaging/odx11.service: RestartForceExitStatus=75
SETTLE_S = 3.0              # dpkg replaces files in bursts: act a little after the last change
RETRY_S = 5.0               # while dpkg is still configuring
GIVE_UP_AFTER = 120         # retries (~10 min): a half-installed package needs the user's attention


def read_version(path: str = VERSION_FILE) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def fingerprint(src_root: str) -> str:
    """sha256 over the daemon's code (odx11/*.py minus GUI_ONLY) and its systemd unit.
    Run at package build time; the result is installed as SERVICE_FILE."""
    h = hashlib.sha256()
    pkg = os.path.join(src_root, "odx11")
    files = sorted(f for f in os.listdir(pkg) if f.endswith(".py") and f not in GUI_ONLY)
    for rel in [os.path.join("odx11", f) for f in files] + ["packaging/odx11.service"]:
        h.update(rel.encode() + b"\0")
        with open(os.path.join(src_root, rel), "rb") as f:
            h.update(f.read() + b"\0")
    return h.hexdigest()


def package_status(package: str = PACKAGE) -> tuple[str, str]:
    """(dpkg status, version), e.g. ("install ok installed", "0.2.2"); ("", "") if unknown."""
    try:
        r = subprocess.run(["dpkg-query", "-W", "-f=${Status}\t${Version}", package],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    status, _, version = r.stdout.partition("\t")
    return status.strip(), version.strip()


def decide(running: str | None, installed: str | None, status: str) -> str:
    """"restart", "wait" (dpkg still busy) or "ignore" (no real change, or removed)."""
    if not running or not installed or installed == running:
        return "ignore"
    if status == "install ok installed":
        return "restart"
    if status.startswith("deinstall") or status.startswith("purge") or not status:
        return "ignore"         # being removed: prerm stops us anyway
    return "wait"               # unpacked / half-configured: dpkg is not done yet


def service_changed(running_fp: str | None, installed_fp: str | None) -> bool:
    """Restart unless both fingerprints are known and equal (an older package has none:
    then any version change restarts, as before)."""
    return not (running_fp and installed_fp and running_fp == installed_fp)


def installed_copy() -> bool:
    """True when this process runs the packaged code, not a source checkout."""
    return os.path.abspath(__file__).startswith("/usr/") and read_version() is not None


class Watcher:
    """Calls on_restart() once the installed version differs from the one we started with."""

    def __init__(self, call_later, on_restart, version_file: str = VERSION_FILE,
                 status=package_status, service_file: str = SERVICE_FILE):
        from gi.repository import Gio
        self.call_later = call_later
        self.on_restart = on_restart
        self.path = version_file
        self.status = status
        self.running = read_version(version_file)
        self.service_file = service_file
        self.running_service = read_version(service_file)      # None for packages built before it existed
        self._pending = None
        self._tries = 0
        # Watch the directory: dpkg replaces the file by renaming a new one over it.
        self._monitor = Gio.File.new_for_path(os.path.dirname(version_file)).monitor_directory(
            Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._monitor.connect("changed", self._on_event)
        log.debug("watching %s for upgrades (running %s)", version_file, self.running)

    def _on_event(self, monitor, f, other, event):
        names = {x.get_basename() for x in (f, other) if x is not None}
        if not names & {os.path.basename(self.path), os.path.basename(self.service_file)}:
            return
        self._schedule(SETTLE_S)

    def _schedule(self, delay):
        if self._pending:
            self._pending()
        self._pending = self.call_later(delay, self._check)

    def _check(self):
        self._pending = None
        status, _ = self.status()
        installed = read_version(self.path)
        verdict = decide(self.running, installed, status)
        if verdict == "restart" and not service_changed(self.running_service, read_version(self.service_file)):
            log.info("updated %s → %s (settings window only) — no restart needed", self.running, installed)
            self.running = installed
            return
        if verdict == "restart":
            log.info("updated %s → %s — restarting onto the new version", self.running, installed)
            self.on_restart()
        elif verdict == "wait":
            self._tries += 1
            if self._tries <= GIVE_UP_AFTER:
                self._schedule(RETRY_S)
            else:
                log.warning("package still not fully installed (%s); not restarting", status)

    def cancel(self):
        if self._pending:
            self._pending()
        self._monitor.cancel()

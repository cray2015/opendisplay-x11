# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""The upgrade watcher on a real folder (inotify), with dpkg and the timer replaced."""
import subprocess
import time
from types import SimpleNamespace

import pytest

from odx11 import upgrade


class Timers:
    def __init__(self):
        self.pending = []

    def call_later(self, seconds, fn):
        entry = [seconds, fn, True]
        self.pending.append(entry)
        return lambda: entry.__setitem__(2, False)

    def fire(self):
        live = [e for e in self.pending if e[2]]
        self.pending = []
        for e in live:
            e[1]()
        return [e[0] for e in live]


def iterate_until(cond, timeout=3):
    from gi.repository import GLib
    ctx, end = GLib.MainContext.default(), time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.005)
    assert cond(), "timed out"


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "VERSION").write_text("0.4.2\n")
    (tmp_path / "SERVICE").write_text("aaa\n")
    return tmp_path


def watcher(folder, status="install ok installed"):
    t, restarts, st = Timers(), [], {"status": status}
    w = upgrade.Watcher(t.call_later, lambda: restarts.append(1), version_file=str(folder / "VERSION"),
                        status=lambda: (st["status"], ""), service_file=str(folder / "SERVICE"))
    return w, t, restarts, st


def replace(folder, name, text):
    tmp = folder / f".{name}.dpkg-new"
    tmp.write_text(text)
    tmp.rename(folder / name)                              # dpkg renames the new file over the old


def test_a_completed_upgrade_of_the_service_restarts_it(folder):
    w, t, restarts, _ = watcher(folder)
    replace(folder, "SERVICE", "bbb\n")
    replace(folder, "VERSION", "0.4.3\n")
    iterate_until(lambda: t.pending)
    assert t.fire() == [upgrade.SETTLE_S]                  # one check, a moment after the last change
    assert restarts == [1]
    w.cancel()


def test_a_window_only_upgrade_does_not_restart(folder, caplog):
    w, t, restarts, _ = watcher(folder)
    caplog.set_level("INFO")
    replace(folder, "VERSION", "0.4.3\n")                  # SERVICE unchanged
    iterate_until(lambda: t.pending)
    t.fire()
    assert restarts == [] and w.running == "0.4.3"
    assert "updated 0.4.2 → 0.4.3 (settings window only) — no restart needed" in caplog.text
    w.cancel()


def test_other_files_in_the_folder_are_ignored(folder):
    w, t, restarts, _ = watcher(folder)
    (folder / "README").write_text("x")
    from gi.repository import GLib
    end = time.monotonic() + 0.3
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    assert t.pending == []
    w.cancel()


def test_while_dpkg_is_busy_it_retries_then_gives_up(folder, monkeypatch, caplog):
    monkeypatch.setattr(upgrade, "GIVE_UP_AFTER", 2)
    w, t, restarts, st = watcher(folder, status="install ok unpacked")
    replace(folder, "VERSION", "0.4.3\n")
    replace(folder, "SERVICE", "bbb\n")
    iterate_until(lambda: t.pending)
    assert t.fire() == [upgrade.SETTLE_S]
    assert t.fire() == [upgrade.RETRY_S]                   # retry 1
    assert t.fire() == [upgrade.RETRY_S]                   # retry 2
    caplog.set_level("WARNING")
    assert t.fire() == []                                  # gave up
    assert "package still not fully installed (install ok unpacked); not restarting" in caplog.text
    assert restarts == []
    w.cancel()


def test_retrying_restarts_once_dpkg_finishes(folder):
    w, t, restarts, st = watcher(folder, status="install ok half-configured")
    replace(folder, "VERSION", "0.4.3\n")
    replace(folder, "SERVICE", "bbb\n")
    iterate_until(lambda: t.pending)
    t.fire()
    st["status"] = "install ok installed"
    t.fire()
    assert restarts == [1]
    w.cancel()


def test_cancel_drops_a_pending_check(folder):
    w, t, restarts, _ = watcher(folder)
    replace(folder, "VERSION", "0.4.3\n")
    iterate_until(lambda: t.pending)
    w.cancel()
    assert t.fire() == []


@pytest.mark.parametrize("content,version", [("0.4.2\n", "0.4.2"), ("\n", None), (None, None)])
def test_read_version(tmp_path, content, version):
    p = tmp_path / "VERSION"
    if content is not None:
        p.write_text(content)
    assert upgrade.read_version(str(p)) == version


@pytest.mark.parametrize("answer,result", [
    (SimpleNamespace(stdout="install ok installed\t0.4.2"), ("install ok installed", "0.4.2")),
    (SimpleNamespace(stdout=""), ("", "")),
    (FileNotFoundError(), ("", "")),
    (subprocess.TimeoutExpired("dpkg-query", 10), ("", "")),
])
def test_package_status(monkeypatch, answer, result):
    calls = []
    def run(cmd, **kw):
        calls.append((cmd, kw))
        if isinstance(answer, BaseException):
            raise answer
        return answer
    monkeypatch.setattr(upgrade.subprocess, "run", run)
    assert upgrade.package_status() == result
    assert calls[0][0] == ["dpkg-query", "-W", "-f=${Status}\t${Version}", "opendisplay-x11"]

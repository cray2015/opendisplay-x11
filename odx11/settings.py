# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""What the settings window shows and writes (PROJECT_SPEC.md §8.8) — pure, no GTK.

Each control maps to config values through the choice lists below, so the
window never offers a value the daemon would reject. Saving writes only the
values that differ from the defaults: the file stays short, and a future
default improves every install that never changed it.
"""
import os
import tempfile
import tomllib
from dataclasses import fields

from . import config
from .display import logical_mode
from .i18n import _

HEADER = ("# OpenDisplay for X11 settings — written by the settings window.\n"
          "# Hand edits are fine and apply at once, but comments are not kept when the\n"
          "# window saves. Every setting, with its default: config.example.toml\n")

# Text size: larger text = fewer, bigger pixels = a larger divisor (§8.2).
TEXT_SIZES = [1.0, 1.25, 1.5, 1.75, 2.0]                      # slider, smaller → larger text
SHARPNESS_QP = [34, 30, 26, 22, 18]                            # slider, softer → sharper
FRAME_RATES = [(15, _("15 frames per second")), (24, _("24 frames per second")), (30, _("30 frames per second"))]
IDLE_PAUSES = [(0.0, _("Never — always stream")), (0.5, _("½ second")), (1.0, _("1 second")),
               (2.0, _("2 seconds")), (5.0, _("5 seconds"))]
LONG_PRESSES = [(300, _("holding ⅓ second")), (500, _("holding ½ second")),
                (750, _("holding ¾ second")), (1000, _("holding 1 second"))]
GRACES = [(5.0, _("5 seconds")), (10.0, _("10 seconds")), (20.0, _("20 seconds")),
          (30.0, _("30 seconds")), (60.0, _("1 minute"))]
DEFAULT_PANEL = (2360, 1640)                                   # until an iPad has been seen


def nearest(choices, value):
    """Index of the choice closest to `value` (choices: values or (value, label) pairs)."""
    vals = [c[0] if isinstance(c, tuple) else c for c in choices]
    return min(range(len(vals)), key=lambda i: abs(vals[i] - value))


def resolution(panel, divisor) -> tuple[int, int]:
    w, h, _ = logical_mode(max(panel), min(panel), divisor)
    return w, h


def port_choices(ports) -> list[tuple[str, str]]:
    """[(connector setting, label)] for the Video port list. `ports` from display.hdmi_ports()."""
    words = {"free": _("free"), "ours": _("in use by OpenDisplay"), "monitor": _("has a monitor")}
    auto = next((n for want in ("ours", "free") for n, st in ports if st == want), None)
    out = [("auto", _("Automatic ({port})").format(port=auto) if auto else _("Automatic (no free port)"))]
    out += [(name, _("{port} — {state}").format(port=name, state=words[st])) for name, st in ports]
    return out


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot write {v!r} to TOML")


def to_toml(cfg) -> str:
    """Canonical TOML of the values in `cfg` that differ from the defaults."""
    defaults = config.parse({})
    out = [HEADER]
    for f in fields(cfg):
        if f.name == "devices":
            continue
        section, default = getattr(cfg, f.name), getattr(defaults, f.name)
        lines = [f"{k.name} = {_toml_value(getattr(section, k.name))}" for k in fields(section)
                 if getattr(section, k.name) != getattr(default, k.name) and getattr(section, k.name) is not None]
        if lines:
            out.append(f"\n[{f.name}]\n" + "\n".join(lines) + "\n")
    for dev_id, dev in sorted(cfg.devices.items()):
        base = config.DeviceConfig()
        lines = [f"{k.name} = {_toml_value(getattr(dev, k.name))}" for k in fields(dev)
                 if getattr(dev, k.name) != getattr(base, k.name) and getattr(dev, k.name) is not None]
        if lines:
            out.append(f"\n[devices.{_toml_value(dev_id)}]\n" + "\n".join(lines) + "\n")
    return "".join(out)


def save(cfg, path: str = config.DEFAULT_PATH):
    """Atomically replace the config file; the daemon picks the change up by itself."""
    text = to_toml(cfg)
    config.parse(tomllib.loads(text))                         # never write what we can't read
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".config-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

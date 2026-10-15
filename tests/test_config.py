# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tomllib

import pytest

from odx11 import config

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "config.example.toml")


def test_example_documents_exactly_the_defaults():
    with open(EXAMPLE, "rb") as f:
        assert config.parse(tomllib.load(f)) == config.parse({})


def test_missing_file_gives_defaults(tmp_path):
    assert config.load(str(tmp_path / "nope.toml")) == config.parse({})


def test_device_table():
    cfg = config.parse({"devices": {"abc": {"side": "left", "align": "center", "mode": [1360, 1024]}}})
    d = cfg.device("abc")
    assert (d.side, d.align, d.mode) == ("left", "center", (1360, 1024))
    assert cfg.device("other") == config.DeviceConfig()


@pytest.mark.parametrize("raw", [
    {"display": {"connectr": "HDMI-A-1"}},              # typo in a key
    {"dispaly": {}},                                     # typo in a section
    {"display": {"connector": "DP-1"}},                  # DP alias fails link training
    {"session": {"ping_interval_s": 3}},                 # receiver would drop us
    {"session": {"ping_interval_s": 2, "silence_timeout_s": 1}},
    {"video": {"keyframe_period": 0}},                   # 0 means every 30 frames
    {"video": {"qp": 60}},
    {"devices": {"x": {"side": "behind"}}},
    {"devices": {"x": {"mode": [1000, 1000]}}},          # not multiples of 16
])
def test_invalid_config_is_rejected(raw):
    with pytest.raises((ValueError, TypeError)):
        config.parse(raw)


def test_service_enabled_by_default_and_switchable():
    assert config.parse({}).service.enabled is True
    assert config.parse({"service": {"enabled": False}}).service.enabled is False


def test_removed_helper_key_is_rejected():
    with pytest.raises(ValueError, match="helper"):
        config.parse({"display": {"helper": "/usr/local/sbin/odx11-connector"}})


def test_presets_override_and_custom_uses_own_values():
    v = config.parse({"video": {"fps": 24, "qp": 34}}).video          # preset defaults to balanced
    assert (v.effective("fps"), v.effective("qp")) == (60, 26)
    v = config.parse({"video": {"preset": "power_saver", "fps": 24}}).video
    assert (v.effective("fps"), v.effective("qp"), v.effective("idle_pause_s")) == (30, 30, 0.5)
    assert v.effective("change_tail_ms") == 100
    v = config.parse({"video": {"preset": "best_quality", "change_tail_ms": 50}}).video
    assert v.effective("change_tail_ms") == 150                       # the preset's, not the stray key
    v = config.parse({"video": {"preset": "custom", "fps": 24, "qp": 34}}).video
    assert (v.effective("fps"), v.effective("qp")) == (24, 34)


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError):
        config.parse({"video": {"preset": "turbo"}})


def test_every_preset_value_is_valid_on_its_own():
    for values in config.PRESETS.values():
        config.parse({"video": {"preset": "custom", **values}})


def test_colour_conversion_choice_is_validated():
    assert config.parse({}).video.convert == "auto"
    assert config.parse({"video": {"convert": "cpu"}}).video.convert == "cpu"
    with pytest.raises(ValueError, match="convert"):
        config.parse({"video": {"convert": "cuda"}})


# Every validation limit, tested exactly at its edge: the first rejected value fails with the
# message that names the key, and the last accepted value parses.
CONNECTOR = 'display.connector must be "auto" or an HDMI-A-n alias (forcing DP-n is untested)'
PRESET = "video.preset must be one of ['balanced', 'best_quality', 'power_saver', 'custom']"
PING = "session.ping_interval_s must be in (0, 2] — the receiver drops silent senders after 5 s"
BOUNDS = [
    # section, key, rejected, accepted, message
    ("display", "divisor", 0, 0.01, "display.divisor must be > 0"),
    ("display", "refresh_hz", 0, 1, "display.refresh_hz must be > 0"),
    ("display", "connect_timeout_s", 0, 0.1, "display timeouts must be positive"),
    ("display", "settle_timeout_s", -0.1, 0, "display timeouts must be positive"),
    ("display", "connector", "DP-1", "HDMI-A-2", CONNECTOR),
    ("video", "fps", 0, 1, "video.fps must be > 0"),
    ("video", "qp", -1, 0, "video.qp must be 0..51"),
    ("video", "qp", 52, 51, "video.qp must be 0..51"),
    ("video", "idle_pause_s", -0.1, 0, "video.idle_pause_s must be >= 0"),
    ("video", "change_tail_ms", -1, 0, "video.change_tail_ms must be 0..5000"),
    ("video", "change_tail_ms", 5001, 5000, "video.change_tail_ms must be 0..5000"),
    ("video", "change_settle_ms", -1, 0, "video.change_settle_ms must be 0..5000"),
    ("video", "change_settle_ms", 5001, 5000, "video.change_settle_ms must be 0..5000"),
    ("video", "input_tail_ms", -1, 0, "video.input_tail_ms must be 0..5000"),
    ("video", "input_tail_ms", 5001, 5000, "video.input_tail_ms must be 0..5000"),
    ("video", "keyframe_period", 0, 1, "video.keyframe_period must be > 0 (0 means every 30 frames)"),
    ("video", "convert", "cuda", "gpu", 'video.convert must be "auto", "gpu" or "cpu"'),
    ("video", "preset", "turbo", "custom", PRESET),
    ("cursor", "max_rate_hz", 0, 1, "cursor.max_rate_hz must be 1..240"),
    ("cursor", "max_rate_hz", 241, 240, "cursor.max_rate_hz must be 1..240"),
    ("input", "scroll_step_px", 0, 1, "input.scroll_step_px must be > 0"),
    ("input", "tap_slop_px", -0.1, 0, "input.tap_slop_px must be >= 0"),
    ("input", "long_press_ms", 0, 1, "input.long_press_ms must be > 0"),
    ("session", "ping_interval_s", 0, 0.1, PING),
    ("session", "ping_interval_s", 2.1, 2.0, PING),
    ("session", "grace_s", -1, 0, "session timings must be positive"),
    ("session", "redial_s", 0, 0.1, "session timings must be positive"),
    ("session", "redial_grace_s", 0, 0.1, "session timings must be positive"),
    ("session", "rotation_debounce_ms", -1, 0, "session timings must be positive"),
]


@pytest.mark.parametrize("section,key,rejected,accepted,message", BOUNDS)
def test_each_limit_at_its_edge(section, key, rejected, accepted, message):
    with pytest.raises(ValueError) as e:
        config.parse({section: {key: rejected}})
    assert str(e.value) == message
    assert getattr(getattr(config.parse({section: {key: accepted}}), section), key) == accepted


def test_silence_timeout_must_exceed_the_ping_interval():
    with pytest.raises(ValueError) as e:
        config.parse({"session": {"ping_interval_s": 2.0, "silence_timeout_s": 2.0}})
    assert str(e.value) == "session.silence_timeout_s must exceed ping_interval_s"
    config.parse({"session": {"ping_interval_s": 2.0, "silence_timeout_s": 2.1}})


@pytest.mark.parametrize("device,ok", [
    ({"side": "behind"}, False),
    ({"align": "middle"}, False),
    ({"side": "below", "align": "end"}, True),
    ({"mode": [1000, 1000]}, False),         # not multiples of 16
    ({"mode": [0, 16]}, False),              # must be positive
    ({"mode": [16]}, False),                 # needs width and height
    ({"mode": [16, 16]}, True),
])
def test_device_table_limits(device, ok):
    if ok:
        config.parse({"devices": {"x": device}})
        return
    with pytest.raises(ValueError, match=r"devices\.x"):
        config.parse({"devices": {"x": device}})


@pytest.mark.parametrize("section", ["service", "notifications", "transport", "display", "video", "cursor",
                                     "input", "session"])
def test_unknown_key_names_its_section(section):
    with pytest.raises(ValueError) as e:
        config.parse({section: {"nope": 1}})
    assert str(e.value) == f"[{section}] unknown keys: ['nope']"


def test_unknown_names_are_reported_by_name():
    with pytest.raises(ValueError, match=r"unknown config sections/keys: \['dispaly'\]"):
        config.parse({"dispaly": {}})
    with pytest.raises(ValueError, match=r"\[display\] unknown keys: \['connectr'\]"):
        config.parse({"display": {"connectr": "HDMI-A-1"}})
    with pytest.raises(ValueError, match=r"\[devices\.x\] unknown keys: \['sde'\]"):
        config.parse({"devices": {"x": {"sde": "left"}}})


def test_auto_connector_is_accepted():
    assert config.parse({"display": {"connector": "auto"}}).display.connector == "auto"

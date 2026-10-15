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

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

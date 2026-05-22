from __future__ import annotations
import copy
import pytest
from ptz_joystick_controller.config import ConfigError, parse_config


def test_valid_config_loads(valid_config_dict):
    config = parse_config(valid_config_dict)
    assert config.switcher.type == "osee_gostream_duet"
    assert config.app.web_port == 8080


def test_invalid_switcher_type_fails(valid_config_dict):
    data = copy.deepcopy(valid_config_dict)
    data["switcher"]["type"] = "unknown"
    with pytest.raises(ConfigError):
        parse_config(data)


def test_source_referencing_unknown_camera_fails(valid_config_dict):
    data = copy.deepcopy(valid_config_dict)
    data["sources"]["mappings"][0]["ptz_camera_id"] = "missing"
    with pytest.raises(ConfigError):
        parse_config(data)

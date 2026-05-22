from __future__ import annotations
import copy
import pytest
from ptz_joystick_controller.config import ConfigError, parse_config
from ptz_joystick_controller.models.joystick import ButtonAction


def test_button_mapping_loads(valid_config_dict):
    config = parse_config(valid_config_dict)
    assert config.joystick.buttons["trigger"].action == ButtonAction.CUT
    assert config.joystick.buttons["button_3"].source_id == "CH1"


def test_preview_source_button_requires_existing_source(valid_config_dict):
    data = copy.deepcopy(valid_config_dict)
    data["joystick"]["buttons"]["button_3"]["source_id"] = "CH99"
    with pytest.raises(ConfigError):
        parse_config(data)


def test_source_id_not_allowed_for_cut(valid_config_dict):
    data = copy.deepcopy(valid_config_dict)
    data["joystick"]["buttons"]["trigger"]["source_id"] = "CH1"
    with pytest.raises(ConfigError):
        parse_config(data)

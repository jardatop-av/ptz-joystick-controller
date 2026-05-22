from __future__ import annotations
from ptz_joystick_controller.presets.manager import PresetManager


def test_load_last_preset(tmp_path, valid_config_dict):
    manager = PresetManager(tmp_path / "presets", tmp_path / "last.txt")
    manager.save_preset("event-a", valid_config_dict, mark_last=True)
    loaded = manager.load_last()
    assert loaded is not None
    assert loaded.app.device_name == "test"
    assert loaded.ptz_camera_for_source("CH1") == "cam1"


def test_load_last_returns_none_when_missing(tmp_path):
    manager = PresetManager(tmp_path / "presets", tmp_path / "last.txt")
    assert manager.load_last() is None

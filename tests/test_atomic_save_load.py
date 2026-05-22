from __future__ import annotations
from ptz_joystick_controller.storage.atomic_write import atomic_write_text, load_text
from ptz_joystick_controller.storage.config_storage import ConfigStorage


def test_atomic_write_and_load_text(tmp_path):
    path = tmp_path / "config.yaml"
    atomic_write_text(path, "hello")
    assert load_text(path) == "hello"
    atomic_write_text(path, "world")
    assert load_text(path) == "world"


def test_config_storage_atomic_save_load(tmp_path, valid_config_dict):
    path = tmp_path / "config.yaml"
    storage = ConfigStorage(path)
    storage.save_raw(valid_config_dict)
    loaded = storage.load()
    assert loaded.app.device_name == "test"
    assert loaded.ptz_camera_for_source("CH2") == "cam2"

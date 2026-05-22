from __future__ import annotations
import copy
import pytest

@pytest.fixture
def valid_config_dict():
    return {
        "app": {"name": "PTZ Joystick Controller", "device_name": "test", "log_level": "info", "web_port": 8080, "auto_load_last_preset": True},
        "switcher": {"type": "osee_gostream_duet", "host": None, "port": None},
        "sources": {"mappings": [
            {"source_id": "CH1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
            {"source_id": "CH2", "display_name": "Camera 2", "ptz_camera_id": "cam2"},
            {"source_id": "MP1", "display_name": "Media", "ptz_camera_id": None},
        ]},
        "ptz": {"cameras": [
            {"id": "cam1", "name": "PTZ 1", "host": None, "port": 52381},
            {"id": "cam2", "name": "PTZ 2", "host": None, "port": 52381},
        ]},
        "joystick": {"type": "logitech_extreme_3d_pro", "device_path": "auto", "deadzone": {"pan": 0.08}, "invert": {"tilt": True}, "buttons": {
            "trigger": {"action": "cut"},
            "thumb": {"action": "copy_program_to_preview"},
            "button_3": {"action": "preview_source", "source_id": "CH1"},
            "button_4": {"action": "none"},
        }},
        "network": {"management_enabled": False, "interface": "eth0", "mode": "dhcp"},
    }

from pathlib import Path

import yaml

from ptz_joystick_controller.config import deep_merge_config, load_config


def test_local_config_overrides_matching_values(tmp_path: Path) -> None:
    base_path = tmp_path / "config.example.yaml"
    local_path = tmp_path / "config.local.yaml"
    base_path.write_text(Path("config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    local_path.write_text(
        """
switcher:
  host: 127.0.0.1
ptz:
  cameras:
    - id: cam1
      host: 192.168.1.110
    - id: cam2
      enabled: false
""".strip(),
        encoding="utf-8",
    )

    config = load_config(base_path)

    assert config.switcher.host == "127.0.0.1"
    cam1 = next(camera for camera in config.ptz.cameras if camera.id == "cam1")
    cam2 = next(camera for camera in config.ptz.cameras if camera.id == "cam2")
    assert cam1.host == "192.168.1.110"
    assert cam1.enabled is True
    assert cam2.enabled is False


def test_local_config_preserves_vmix_input_1_to_4_mapping(tmp_path: Path) -> None:
    base_path = tmp_path / "config.example.yaml"
    local_path = tmp_path / "config.local.yaml"
    base_path.write_text(Path("config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    local_path.write_text(
        """
sources:
  mappings:
    - source_id: Input 1
      ptz_camera_id: cam1
    - source_id: Input 2
      ptz_camera_id: null
""".strip(),
        encoding="utf-8",
    )

    config = load_config(base_path)

    assert config.sources.source_ids() >= {"Input 1", "Input 2", "Input 3", "Input 4"}
    assert config.sources.camera_for_source("Input 1") == "cam1"
    assert config.sources.camera_for_source("Input 2") is None
    assert config.sources.camera_for_source("Input 3") is None
    assert config.sources.camera_for_source("Input 4") is None


def test_load_config_can_disable_local_overrides(tmp_path: Path) -> None:
    base_path = tmp_path / "config.example.yaml"
    local_path = tmp_path / "config.local.yaml"
    base_path.write_text(Path("config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    local_path.write_text("switcher:\n  host: 127.0.0.1\n", encoding="utf-8")

    config = load_config(base_path, use_local=False)

    assert config.switcher.host is None


def test_deep_merge_config_merges_lists_by_stable_id() -> None:
    base = {
        "ptz": {
            "cameras": [
                {"id": "cam1", "host": None, "enabled": True, "port": 52381},
                {"id": "cam2", "host": None, "enabled": True, "port": 52381},
            ]
        }
    }
    override = {"ptz": {"cameras": [{"id": "cam2", "enabled": False}]}}

    merged = deep_merge_config(base, override)

    assert merged["ptz"]["cameras"][0]["id"] == "cam1"
    assert merged["ptz"]["cameras"][1] == {
        "id": "cam2",
        "host": None,
        "enabled": False,
        "port": 52381,
    }


def test_config_local_example_is_valid_when_merged() -> None:
    config = load_config("config.example.yaml", local_path="config.local.example.yaml")

    assert config.switcher.host == "127.0.0.1"
    assert config.ptz_camera_for_source("Input 1") == "cam1"
    assert config.ptz_camera_for_source("Input 3") is None


from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.joystick.button_metadata import CANONICAL_BUTTON_IDS
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


def _write_example(tmp_path: Path) -> Path:
    source = Path("config.example.yaml")
    target = tmp_path / "config.example.yaml"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _client(tmp_path: Path) -> tuple[TestClient, Path, Path]:
    example = _write_example(tmp_path)
    local = tmp_path / "config.local.yaml"
    config = load_config(example, local_path=local)
    provider = RuntimeStatusProvider(
        state=AppState(config=config),
        event_bus=EventBus(),
        started_at=datetime.now(timezone.utc),
    )
    return TestClient(create_web_app(provider, config_example_path=example, config_local_path=local)), example, local


def _payload(client: TestClient) -> dict[str, object]:
    response = client.get("/api/config")
    assert response.status_code == 200
    return response.json()["editable_config"]


def test_config_page_returns_form_fields(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    response = client.get("/config")
    assert response.status_code == 200
    text = response.text
    assert "Basic configuration" in text
    assert "switcher_host" in text
    assert "preset_offset" in text
    assert "button_button_7_action" in text
    assert "Advanced YAML editor" in text


def test_form_save_writes_local_config(tmp_path: Path) -> None:
    client, example, local = _client(tmp_path)
    original_example = example.read_text(encoding="utf-8")
    data = {
        "switcher_host": "127.0.0.1",
        "switcher_port": "8088",
        "camera_0_id": "cam1",
        "camera_0_name": "Camera One",
        "camera_0_host": "192.168.1.110",
        "camera_0_port": "52381",
        "camera_0_enabled": "on",
        "camera_0_preset_offset": "0",
        "camera_1_id": "cam2",
        "camera_1_name": "PTZ Camera 2",
        "camera_1_host": "",
        "camera_1_port": "52381",
        "camera_1_preset_offset": "0",
        "invert_tilt": "on",
        "output_deadzone_pan_tilt": "0.05",
        "output_deadzone_zoom": "0.05",
        "stop_watchdog_enabled": "on",
        "center_confirm_samples": "3",
    }
    for button_id in ("trigger", "thumb", "button_3", "button_4", "button_5", "button_6", "button_7", "button_8", "button_9", "button_10", "button_11", "button_12"):
        data[f"button_{button_id}_action"] = "none"
    data["button_trigger_action"] = "cut"
    data["button_thumb_action"] = "copy_program_to_preview"
    data["button_button_3_action"] = "preview_source"
    data["button_button_3_source_id"] = "Input 1"
    data["button_button_7_action"] = "preset_recall"
    data["button_button_7_preset_number"] = "1"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    assert "Configuration saved. Restart required." in response.text
    assert local.exists()
    assert example.read_text(encoding="utf-8") == original_example
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["switcher"]["host"] == "127.0.0.1"
    assert written["ptz"]["cameras"][0]["name"] == "Camera One"
    assert written["ptz"]["cameras"][1]["enabled"] is False
    assert written["joystick"]["buttons"]["button_7"]["preset_number"] == 1


def test_form_invalid_port_rejected(tmp_path: Path) -> None:
    client, _, local = _client(tmp_path)
    payload = _payload(client)
    payload["switcher"]["port"] = 70000  # type: ignore[index]
    response = client.post("/config/basic", json=payload)
    assert response.status_code == 400
    assert not local.exists()


def test_form_invalid_action_rejected(tmp_path: Path) -> None:
    client, _, local = _client(tmp_path)
    payload = _payload(client)
    payload["joystick"]["buttons"]["button_10"] = {"action": "explode"}  # type: ignore[index]
    response = client.post("/config/basic", json=payload)
    assert response.status_code == 400
    assert not local.exists()


def test_raw_yaml_editor_still_works_and_backup_created(tmp_path: Path) -> None:
    client, _, local = _client(tmp_path)
    local.write_text("switcher:\n  host: old.local\n", encoding="utf-8")
    payload = _payload(client)
    payload["switcher"]["host"] = "raw.local"  # type: ignore[index]
    raw_yaml = yaml.safe_dump(payload, sort_keys=False)

    response = client.post("/config/raw", data={"raw_yaml": raw_yaml})

    assert response.status_code == 200
    assert (tmp_path / "config.local.yaml.bak").exists()
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["switcher"]["host"] == "raw.local"


def test_dashboard_still_works_stage35(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    assert client.get("/").status_code == 200


def _client_with_existing_local(tmp_path: Path, local_text: str) -> tuple[TestClient, Path, Path]:
    example = _write_example(tmp_path)
    local = tmp_path / "config.local.yaml"
    local.write_text(local_text, encoding="utf-8")
    config = load_config(example, local_path=local)
    provider = RuntimeStatusProvider(
        state=AppState(config=config),
        event_bus=EventBus(),
        started_at=datetime.now(timezone.utc),
    )
    return TestClient(create_web_app(provider, config_example_path=example, config_local_path=local)), example, local


def _minimal_stage35_form(client: TestClient) -> dict[str, str]:
    editable = _payload(client)
    data: dict[str, str] = {
        "switcher_host": str(editable["switcher"]["host"] or ""),  # type: ignore[index]
        "switcher_port": str(editable["switcher"]["port"] or ""),  # type: ignore[index]
        "output_deadzone_pan_tilt": str(editable["joystick"]["output_deadzone"]["pan_tilt"]),  # type: ignore[index]
        "output_deadzone_zoom": str(editable["joystick"]["output_deadzone"]["zoom"]),  # type: ignore[index]
        "center_confirm_samples": str(editable["ptz"]["stop_watchdog"]["center_confirm_samples"]),  # type: ignore[index]
    }
    if editable["joystick"]["invert"]["pan"]:  # type: ignore[index]
        data["invert_pan"] = "on"
    if editable["joystick"]["invert"]["tilt"]:  # type: ignore[index]
        data["invert_tilt"] = "on"
    if editable["joystick"]["invert"]["zoom"]:  # type: ignore[index]
        data["invert_zoom"] = "on"
    if editable["ptz"]["stop_watchdog"]["enabled"]:  # type: ignore[index]
        data["stop_watchdog_enabled"] = "on"

    for index, camera in enumerate(editable["ptz"]["cameras"]):  # type: ignore[index]
        data[f"camera_{index}_id"] = str(camera["id"])
        data[f"camera_{index}_name"] = str(camera["name"])
        data[f"camera_{index}_host"] = str(camera.get("host") or "")
        data[f"camera_{index}_port"] = str(camera["port"])
        if camera.get("enabled"):
            data[f"camera_{index}_enabled"] = "on"
        data[f"camera_{index}_preset_offset"] = str(camera.get("preset_offset", 0))

    buttons = editable["joystick"]["buttons"]  # type: ignore[index]
    for button_id in CANONICAL_BUTTON_IDS:
        button = buttons[button_id]
        action = str(button.get("action", "none"))
        data[f"button_{button_id}_action"] = action
        if "source_id" in button and button.get("source_id") is not None:
            data[f"button_{button_id}_source_id"] = str(button["source_id"])
        if "preset_number" in button and button.get("preset_number") is not None:
            data[f"button_{button_id}_preset_number"] = str(button["preset_number"])
    return data


def test_form_save_preserves_existing_webui_section(tmp_path: Path) -> None:
    client, _, local = _client_with_existing_local(
        tmp_path,
        """
webui:
  enabled: true
  listen_host: 127.0.0.1
  listen_port: 8080
""".strip()
        + "\n",
    )
    data = _minimal_stage35_form(client)
    data["switcher_host"] = "127.0.0.1"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["webui"]["enabled"] is True
    assert written["webui"]["listen_host"] == "127.0.0.1"
    assert written["webui"]["listen_port"] == 8080


def test_form_save_preserves_unknown_section(tmp_path: Path) -> None:
    client, _, local = _client_with_existing_local(
        tmp_path,
        """
custom_user_extension:
  keep: true
  note: do not delete me
""".strip()
        + "\n",
    )
    data = _minimal_stage35_form(client)
    data["switcher_host"] = "vmix.local"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["custom_user_extension"] == {"keep": True, "note": "do not delete me"}


def test_form_save_changes_only_edited_controlled_fields(tmp_path: Path) -> None:
    client, _, local = _client_with_existing_local(
        tmp_path,
        """
webui:
  listen_host: 127.0.0.1
custom_user_extension:
  keep: true
switcher:
  host: old.local
""".strip()
        + "\n",
    )
    data = _minimal_stage35_form(client)
    data["switcher_host"] = "new.local"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["switcher"]["host"] == "new.local"
    assert written["webui"]["listen_host"] == "127.0.0.1"
    assert written["custom_user_extension"]["keep"] is True


def test_form_save_preserve_unknown_backup_still_created(tmp_path: Path) -> None:
    client, _, local = _client_with_existing_local(
        tmp_path,
        """
webui:
  listen_host: 127.0.0.1
unknown_section:
  value: old
""".strip()
        + "\n",
    )
    data = _minimal_stage35_form(client)
    data["switcher_host"] = "backup-test.local"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    backup = tmp_path / "config.local.yaml.bak"
    assert backup.exists()
    backup_data = yaml.safe_load(backup.read_text(encoding="utf-8"))
    assert backup_data["unknown_section"]["value"] == "old"


def test_form_save_updates_button_10_preset_recall_and_preserves_sections(tmp_path: Path) -> None:
    client, _, local = _client_with_existing_local(
        tmp_path,
        """
webui:
  enabled: true
  listen_host: 127.0.0.1
custom_user_extension:
  keep: true
joystick:
  buttons:
    button_10:
      action: none
""".strip()
        + "\n",
    )
    data = _minimal_stage35_form(client)
    data["button_button_10_action"] = "preset_recall"
    data["button_button_10_preset_number"] = "4"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    assert "Configuration saved. Restart required." in response.text
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["joystick"]["buttons"]["button_10"] == {
        "action": "preset_recall",
        "preset_number": 4,
    }
    assert written["webui"]["listen_host"] == "127.0.0.1"
    assert written["custom_user_extension"]["keep"] is True


def test_form_save_updates_button_3_source_id(tmp_path: Path) -> None:
    client, _, local = _client_with_existing_local(
        tmp_path,
        """
webui:
  listen_host: 127.0.0.1
joystick:
  buttons:
    button_3:
      action: preview_source
      source_id: Input 1
""".strip()
        + "\n",
    )
    data = _minimal_stage35_form(client)
    data["button_button_3_action"] = "preview_source"
    data["button_button_3_source_id"] = "Input 4"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["joystick"]["buttons"]["button_3"] == {
        "action": "preview_source",
        "source_id": "Input 4",
    }
    assert written["webui"]["listen_host"] == "127.0.0.1"


def test_form_save_action_none_clears_irrelevant_button_fields(tmp_path: Path) -> None:
    client, _, local = _client_with_existing_local(
        tmp_path,
        """
unknown_section:
  keep: true
joystick:
  buttons:
    button_10:
      action: preset_recall
      preset_number: 4
""".strip()
        + "\n",
    )
    data = _minimal_stage35_form(client)
    data["button_button_10_action"] = "none"
    data["button_button_10_preset_number"] = "4"
    data["button_button_10_source_id"] = "Input 1"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["joystick"]["buttons"]["button_10"] == {"action": "none"}
    assert written["unknown_section"]["keep"] is True


def test_form_save_accepts_natural_button_field_name_fallback(tmp_path: Path) -> None:
    client, _, local = _client(tmp_path)
    data = _minimal_stage35_form(client)
    data.pop("button_button_10_action", None)
    data.pop("button_button_10_preset_number", None)
    data["button_10_action"] = "preset_recall"
    data["button_10_preset_number"] = "4"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["joystick"]["buttons"]["button_10"]["action"] == "preset_recall"
    assert written["joystick"]["buttons"]["button_10"]["preset_number"] == 4

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


def _write_example(tmp_path: Path) -> Path:
    source = Path("config.example.yaml")
    target = tmp_path / "config.example.yaml"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _client(tmp_path: Path) -> tuple[TestClient, AppState, Path, Path]:
    example = _write_example(tmp_path)
    local = tmp_path / "config.local.yaml"
    config = load_config(example, local_path=local)
    state = AppState(config=config)
    provider = RuntimeStatusProvider(
        state=state,
        event_bus=EventBus(),
        started_at=datetime.now(timezone.utc),
    )
    client = TestClient(create_web_app(provider, config_example_path=example, config_local_path=local))
    return client, state, local, example


def _editable(client: TestClient) -> dict:
    response = client.get("/api/config")
    assert response.status_code == 200
    return response.json()["editable_config"]


def test_enabled_camera_without_host_is_rejected_with_friendly_error(tmp_path: Path) -> None:
    client, _state, local, _example = _client(tmp_path)
    payload = _editable(client)
    payload["ptz"]["cameras"][1]["enabled"] = True
    payload["ptz"]["cameras"][1]["host"] = ""

    response = client.post("/config", json=payload)

    assert response.status_code == 400
    assert response.json()["error"] == "Camera cam2 is enabled but host is empty."
    assert not local.exists()


def test_invalid_enabled_camera_without_host_is_not_written_from_form(tmp_path: Path) -> None:
    client, _state, local, _example = _client(tmp_path)
    response = client.post(
        "/config/basic",
        data={
            "switcher_host": "127.0.0.1",
            "switcher_port": "8088",
            "camera_0_id": "cam1",
            "camera_0_name": "PTZ Camera 1",
            "camera_0_host": "",
            "camera_0_port": "52381",
            "camera_0_enabled": "on",
            "camera_0_preset_offset": "0",
            "camera_1_id": "cam2",
            "camera_1_name": "PTZ Camera 2",
            "camera_1_host": "192.0.2.22",
            "camera_1_port": "52381",
            "camera_1_enabled": "on",
            "camera_1_preset_offset": "0",
            "output_deadzone_pan_tilt": "0.05",
            "output_deadzone_zoom": "0.05",
            "center_confirm_samples": "3",
            "stop_watchdog_enabled": "on",
        },
    )

    assert response.status_code == 400
    assert "Camera cam1 is enabled but host is empty." in response.text
    assert not local.exists()


def test_invalid_enabled_camera_without_host_is_not_applied(tmp_path: Path) -> None:
    client, state, local, _example = _client(tmp_path)
    original_config = state.config
    local.write_text(
        """
ptz:
  cameras:
    - id: cam2
      enabled: true
      host: ''
""".strip()
        + "\n",
        encoding="utf-8",
    )

    response = client.post("/api/config/apply")

    assert response.status_code == 400
    assert response.json()["error"] == "Camera cam2 is enabled but host is empty."
    assert state.config is original_config


def test_enabled_camera_with_host_but_offline_is_accepted(tmp_path: Path) -> None:
    client, _state, local, _example = _client(tmp_path)
    payload = _editable(client)
    payload["ptz"]["cameras"][1]["enabled"] = True
    payload["ptz"]["cameras"][1]["host"] = "192.0.2.222"

    response = client.post("/config", json=payload)

    assert response.status_code == 200
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["ptz"]["cameras"][1]["enabled"] is True
    assert written["ptz"]["cameras"][1]["host"] == "192.0.2.222"


def test_enabled_camera_without_host_returns_400_not_internal_error(tmp_path: Path) -> None:
    client, _state, _local, _example = _client(tmp_path)
    payload = _editable(client)
    payload["ptz"]["cameras"][1]["enabled"] = True
    payload["ptz"]["cameras"][1]["host"] = ""

    response = client.post("/config", json=payload)

    assert response.status_code == 400
    assert response.json()["status"] == "error"

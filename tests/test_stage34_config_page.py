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


def _editable_payload(client: TestClient) -> dict[str, object]:
    response = client.get("/api/config")
    assert response.status_code == 200
    payload = response.json()["editable_config"]
    assert isinstance(payload, dict)
    return payload


def test_config_page_returns_200(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    response = client.get("/config")
    assert response.status_code == 200
    assert "Configuration" in response.text
    assert "config.local.yaml" in response.text


def test_save_writes_local_config_not_example(tmp_path: Path) -> None:
    client, example, local = _client(tmp_path)
    original_example = example.read_text(encoding="utf-8")
    payload = _editable_payload(client)
    payload["switcher"]["host"] = "127.0.0.1"  # type: ignore[index]
    payload["ptz"]["cameras"][0]["host"] = "192.168.1.110"  # type: ignore[index]

    response = client.post("/config", json=payload)

    assert response.status_code == 200
    assert response.json()["message"] == "Configuration saved. Restart required."
    assert local.exists()
    assert example.read_text(encoding="utf-8") == original_example
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["switcher"]["host"] == "127.0.0.1"
    assert written["ptz"]["cameras"][0]["host"] == "192.168.1.110"


def test_invalid_action_is_rejected(tmp_path: Path) -> None:
    client, _, local = _client(tmp_path)
    payload = _editable_payload(client)
    payload["joystick"]["buttons"]["button_10"] = {"action": "explode"}  # type: ignore[index]

    response = client.post("/config", json=payload)

    assert response.status_code == 400
    assert not local.exists()
    assert "explode" in response.json()["error"]


def test_invalid_port_is_rejected(tmp_path: Path) -> None:
    client, _, local = _client(tmp_path)
    payload = _editable_payload(client)
    payload["switcher"]["port"] = 70000  # type: ignore[index]

    response = client.post("/config", json=payload)

    assert response.status_code == 400
    assert not local.exists()
    assert "65535" in response.json()["error"]


def test_backup_is_created(tmp_path: Path) -> None:
    client, _, local = _client(tmp_path)
    local.write_text("switcher:\n  host: old.local\n", encoding="utf-8")
    payload = _editable_payload(client)
    payload["switcher"]["host"] = "new.local"  # type: ignore[index]

    response = client.post("/config", json=payload)

    assert response.status_code == 200
    backup = tmp_path / "config.local.yaml.bak"
    assert backup.exists()
    assert "old.local" in backup.read_text(encoding="utf-8")


def test_dashboard_still_works(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    assert client.get("/").status_code == 200
    status = client.get("/api/status")
    assert status.status_code == 200
    assert "joystick" in status.json()

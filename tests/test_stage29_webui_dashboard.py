from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.models.joystick_runtime import JoystickHealth
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


def _provider() -> RuntimeStatusProvider:
    config = load_config("config.example.yaml", use_local=False)
    state = AppState(config=config)
    state.program_source_id = "Input 1"
    state.preview_source_id = "Input 2"
    state.recompute_active_ptz()
    event_bus = EventBus()
    provider = RuntimeStatusProvider(
        state=state,
        event_bus=event_bus,
        joystick_health=JoystickHealth(),
        started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )
    event_bus.publish("test.event", {"value": 1})
    return provider


def test_dashboard_route_returns_200() -> None:
    client = TestClient(create_web_app(_provider()))
    response = client.get("/")
    assert response.status_code == 200
    assert "PTZ Joystick Controller" in response.text
    assert "/api/status" in response.text


def test_api_status_returns_expected_structure() -> None:
    client = TestClient(create_web_app(_provider()))
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert {"joystick", "switcher", "ptz", "preview", "program", "active_ptz_camera", "uptime"}.issubset(data)
    assert data["program"] == "Input 1"
    assert data["preview"] == "Input 2"
    assert data["active_ptz_camera"] == "cam2"
    assert data["safety"]["output_deadzone"]["pan_tilt"] == 0.05
    assert data["recent_activity"][0]["type"] == "test.event"


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(create_web_app(_provider()))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_disconnected_joystick_state_renders_correctly() -> None:
    client = TestClient(create_web_app(_provider()))
    data = client.get("/api/status").json()
    assert data["joystick"]["connected"] is False
    assert data["joystick"]["state"] == "disconnected"
    assert data["joystick"]["device_name"] is None
    assert data["joystick"]["pressed_buttons"] == []
    assert data["joystick"]["hat"]["direction"] == "center"


def test_dashboard_does_not_crash_without_switcher_or_ptz_router() -> None:
    provider = _provider()
    provider.switcher = None
    provider.ptz_router = None
    client = TestClient(create_web_app(provider))
    status = client.get("/api/status")
    dashboard = client.get("/")
    assert status.status_code == 200
    assert dashboard.status_code == 200
    data = status.json()
    assert data["switcher"]["connected"] is False
    assert data["ptz"]["configured_cameras"]

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.commands import CommandType
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge
from ptz_joystick_controller.switchers.fake import FakeSwitcher
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


class StaticFakeJoystickDiscovery:
    def discover(self):
        return [JoystickDeviceInfo(name="Fake Joystick", path="fake0", backend="fake")]


def _write_example(tmp_path: Path) -> Path:
    target = tmp_path / "config.example.yaml"
    target.write_text(Path("config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _runtime_client(tmp_path: Path):
    example = _write_example(tmp_path)
    local = tmp_path / "config.local.yaml"
    local.write_text(
        """
webui:
  enabled: true
  listen_host: 127.0.0.1
  listen_port: 8080
unknown_section:
  keep: true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    config = load_config(example, local_path=local)
    bus = EventBus()
    provider = FakeJoystickInputProvider()
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=StaticFakeJoystickDiscovery(),
        provider_factory=lambda _device: provider,
    )
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 3", preview_source_id="Input 1")
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus, dry_run=True)
    bridge.start()
    status_provider = RuntimeStatusProvider.from_bridge(bridge)
    client = TestClient(create_web_app(status_provider, config_example_path=example, config_local_path=local))
    return client, bridge, local, example


def _editable(client: TestClient) -> dict:
    response = client.get("/api/config")
    assert response.status_code == 200
    return response.json()["editable_config"]


def _form_from_editable(client: TestClient) -> dict[str, str]:
    editable = _editable(client)
    data: dict[str, str] = {
        "switcher_host": str(editable["switcher"]["host"] or ""),
        "switcher_port": str(editable["switcher"]["port"] or ""),
        "output_deadzone_pan_tilt": str(editable["joystick"]["output_deadzone"]["pan_tilt"]),
        "output_deadzone_zoom": str(editable["joystick"]["output_deadzone"]["zoom"]),
        "center_confirm_samples": str(editable["ptz"]["stop_watchdog"]["center_confirm_samples"]),
    }
    if editable["joystick"]["invert"].get("pan"):
        data["invert_pan"] = "on"
    if editable["joystick"]["invert"].get("tilt"):
        data["invert_tilt"] = "on"
    if editable["joystick"]["invert"].get("zoom"):
        data["invert_zoom"] = "on"
    if editable["ptz"]["stop_watchdog"].get("enabled"):
        data["stop_watchdog_enabled"] = "on"
    for index, camera in enumerate(editable["ptz"]["cameras"]):
        data[f"camera_{index}_id"] = str(camera["id"])
        data[f"camera_{index}_name"] = str(camera["name"])
        data[f"camera_{index}_host"] = str(camera.get("host") or "")
        data[f"camera_{index}_port"] = str(camera["port"])
        if camera.get("enabled"):
            data[f"camera_{index}_enabled"] = "on"
        data[f"camera_{index}_preset_offset"] = str(camera.get("preset_offset", 0))
    for button_id, mapping in editable["joystick"]["buttons"].items():
        data[f"button_{button_id}_action"] = str(mapping.get("action", "none"))
        if mapping.get("source_id") is not None:
            data[f"button_{button_id}_source_id"] = str(mapping["source_id"])
        if mapping.get("preset_number") is not None:
            data[f"button_{button_id}_preset_number"] = str(mapping["preset_number"])
    return data


def test_save_only_still_requires_restart_and_does_not_update_runtime_mapping(tmp_path: Path) -> None:
    client, bridge, _local, _example = _runtime_client(tmp_path)
    before = bridge.joystick_dispatcher.command_for_button("button_10")
    assert before.type == CommandType.NOOP
    data = _form_from_editable(client)
    data["button_button_10_action"] = "preset_recall"
    data["button_button_10_preset_number"] = "4"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    assert "Restart required" in response.text
    after = bridge.joystick_dispatcher.command_for_button("button_10")
    assert after.type == CommandType.NOOP
    assert client.get("/api/status").json()["config"]["pending_changes"] is True


def test_save_and_apply_updates_button_mapping_without_process_restart(tmp_path: Path) -> None:
    client, bridge, local, _example = _runtime_client(tmp_path)
    data = _form_from_editable(client)
    data["button_button_10_action"] = "preset_recall"
    data["button_button_10_preset_number"] = "4"
    data["apply"] = "1"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    assert "Configuration applied" in response.text
    command = bridge.joystick_dispatcher.command_for_button("button_10")
    assert command.type == CommandType.PTZ_PRESET_RECALL
    assert command.preset_number == 4
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["joystick"]["buttons"]["button_10"] == {"action": "preset_recall", "preset_number": 4}


def test_invalid_config_apply_does_not_replace_current_runtime_config(tmp_path: Path) -> None:
    client, bridge, local, _example = _runtime_client(tmp_path)
    current_port = bridge.state.config.switcher.port
    local.write_text("switcher:\n  port: 70000\n", encoding="utf-8")

    response = client.post("/api/config/apply")

    assert response.status_code == 400
    assert bridge.state.config.switcher.port == current_port
    assert client.get("/api/status").json()["config"]["last_apply_result"] == "error"


def test_apply_sends_ptz_safe_stop_before_rebuild(tmp_path: Path) -> None:
    client, bridge, local, _example = _runtime_client(tmp_path)
    bridge.state.preview_source_id = "Input 1"
    bridge.state.recompute_active_ptz()
    bridge.ptz_router.route_velocity(bridge.joystick_monitor.ptz_velocity(provider_snapshot := bridge.joystick_monitor.health.last_snapshot))
    # Force a tracked moving state without relying on raw calibration details.
    bridge.ptz_router.pan_tilt_active = True
    bridge.ptz_router.zoom_active = True
    local.write_text("switcher:\n  host: 127.0.0.1\n", encoding="utf-8")

    response = client.post("/api/config/apply")

    assert response.status_code == 200
    assert any("pan_tilt_stop reason=config_apply" in entry for entry in bridge.ptz_router.command_log)
    assert any("zoom_stop reason=config_apply" in entry for entry in bridge.ptz_router.command_log)


def test_apply_preserves_webui_and_dashboard_still_works(tmp_path: Path) -> None:
    client, bridge, local, _example = _runtime_client(tmp_path)
    data = _form_from_editable(client)
    data["switcher_host"] = "127.0.0.1"
    data["apply"] = "1"

    response = client.post("/config/basic", data=data)

    assert response.status_code == 200
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["webui"]["listen_host"] == "127.0.0.1"
    assert written["unknown_section"]["keep"] is True
    assert client.get("/").status_code == 200
    status = client.get("/api/status").json()
    assert status["config"]["last_apply_result"] == "ok"
    assert bridge.state.config.switcher.host == "127.0.0.1"

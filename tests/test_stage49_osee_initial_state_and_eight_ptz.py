from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import load_config, parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.runtime.switcher_executor import SwitcherCommandExecutor
from ptz_joystick_controller.state_machine.preview_program import PreviewProgramStateMachine
from ptz_joystick_controller.state_machine.ptz_control import PtzControlStateMachine
from ptz_joystick_controller.switchers.osee_gsp import GspCommand, decode_gsp_packet, encode_gsp_command
from ptz_joystick_controller.switchers.osee_gostream_duet import OseeGoStreamDuetSwitcher
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


class FakeTransport:
    def __init__(self) -> None:
        self.connected = False
        self.sent: list[GspCommand] = []
        self.incoming: deque[tuple[GspCommand, ...]] = deque()

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.connected = True

    def send_command(self, command: GspCommand) -> bytes:
        self.sent.append(command)
        return encode_gsp_command(command)

    def send_get(self, command_id: str) -> bytes:
        return self.send_command(GspCommand(id=command_id, type="get"))

    def receive(self) -> tuple[GspCommand, ...]:
        return self.incoming.popleft() if self.incoming else ()


def _osee_config():
    return parse_config({
        "switcher": {"type": "osee", "host": "192.0.2.10", "port": 19010},
        "ptz": {"cameras": [
            {"id": "cam1", "name": "Cam 1", "host": "192.0.2.101", "enabled": True},
            {"id": "cam2", "name": "Cam 2", "host": None, "enabled": False},
        ]},
        "sources": {"mappings": [
            {"source_id": "Input 1", "ptz_camera_id": "cam1"},
            {"source_id": "Input 2", "ptz_camera_id": "cam2"},
        ]},
    })


def test_parser_accepts_response_type_res() -> None:
    command = GspCommand(id="pvwIndex", type="res", value=(1,))
    assert decode_gsp_packet(encode_gsp_command(command)) == command


def test_res_initializes_program_preview_transition_and_ptz() -> None:
    cfg = _osee_config()
    transport = FakeTransport()
    switcher = OseeGoStreamDuetSwitcher(cfg.switcher.host or "", 19010, transport_factory=lambda h, p: transport)
    switcher.connect()
    bus = EventBus()
    state = AppState(cfg)
    ptz = PtzControlStateMachine(state, bus)
    preview = PreviewProgramStateMachine(state, bus, ptz)
    executor = SwitcherCommandExecutor(switcher, state, bus, preview, ptz)
    transport.incoming.append((
        GspCommand(id="pgmIndex", type="res", value=(2,)),
        GspCommand(id="pvwIndex", type="res", value=(1,)),
        GspCommand(id="transitionStatus", type="res", value=(0,)),
    ))

    executor.sync_from_switcher()

    assert state.program_source_id == "Input 2"
    assert state.preview_source_id == "Input 1"
    assert state.transition_state == (0,)
    assert state.active_ptz_camera_id == "cam1"


def test_push_updates_continue_after_initial_res() -> None:
    cfg = _osee_config()
    transport = FakeTransport()
    switcher = OseeGoStreamDuetSwitcher(cfg.switcher.host or "", 19010, transport_factory=lambda h, p: transport)
    switcher.connect()
    transport.incoming.append((GspCommand(id="pvwIndex", type="res", value=(1,)),))
    switcher.poll()
    transport.incoming.append((GspCommand(id="pvwIndex", type="pus", value=(2,)),))
    switcher.poll()
    assert switcher.get_preview_source() == "Input 2"


def test_default_osee_migration_adds_eight_cameras_and_mappings() -> None:
    cfg = _osee_config()
    assert {camera.id for camera in cfg.ptz.cameras} == {f"cam{n}" for n in range(1, 9)}
    for n in range(1, 9):
        assert cfg.sources.camera_for_source(f"Input {n}") == f"cam{n}"
    assert cfg.sources.camera_for_source("MP1") is None
    assert cfg.sources.camera_for_source("MP2") is None
    assert cfg.sources.camera_for_source("M/SRC") is None
    assert next(camera for camera in cfg.ptz.cameras if camera.id == "cam3").enabled is False


def test_old_two_camera_osee_config_remains_valid_and_is_augmented() -> None:
    cfg = _osee_config()
    cam1 = next(camera for camera in cfg.ptz.cameras if camera.id == "cam1")
    assert cam1.host == "192.0.2.101"
    assert len(cfg.ptz.cameras) == 8


def test_web_form_displays_and_preserves_eight_osee_camera_slots(tmp_path: Path) -> None:
    example = tmp_path / "config.example.yaml"
    base = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    base["switcher"] = {"type": "osee", "host": "192.0.2.10", "port": 19010}
    example.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    local = tmp_path / "config.local.yaml"
    cfg = load_config(example, local_path=local)
    provider = RuntimeStatusProvider(state=AppState(cfg), event_bus=EventBus(), started_at=datetime.now(timezone.utc))
    client = TestClient(create_web_app(provider, config_example_path=example, config_local_path=local))

    page = client.get("/config")
    assert page.status_code == 200
    for n in range(1, 9):
        assert f"Input {n}" in page.text
        assert f"cam{n}" in page.text

    payload = client.get("/api/config").json()["editable_config"]
    assert len(payload["ptz"]["cameras"]) == 8
    payload["ptz"]["cameras"][2]["name"] = "Camera Three"
    response = client.post("/config/basic", json=payload)
    assert response.status_code == 200
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert len(written["ptz"]["cameras"]) == 8
    assert next(item for item in written["ptz"]["cameras"] if item["id"] == "cam3")["name"] == "Camera Three"


def test_vmix_minimal_config_is_not_augmented_with_osee_mappings() -> None:
    cfg = parse_config({
        "switcher": {"type": "vmix", "host": "127.0.0.1"},
        "ptz": {"cameras": [{"id": "cam1", "name": "Cam 1", "enabled": False}]},
        "sources": {"mappings": [{"source_id": "Input 1", "ptz_camera_id": "cam1"}]},
    })
    assert len(cfg.ptz.cameras) == 1
    assert cfg.sources.source_ids() == {"Input 1"}


def test_local_osee_selection_gets_default_input_mappings_but_explicit_none_wins(tmp_path: Path) -> None:
    example = tmp_path / "config.example.yaml"
    example.write_text(Path("config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    local = tmp_path / "config.local.yaml"
    local.write_text(
        """
switcher:
  type: osee
  host: 192.0.2.10
sources:
  mappings:
    - source_id: Input 4
      ptz_camera_id: null
""".strip(),
        encoding="utf-8",
    )
    cfg = load_config(example, local_path=local)
    assert cfg.sources.camera_for_source("Input 3") == "cam3"
    assert cfg.sources.camera_for_source("Input 4") is None

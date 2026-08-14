from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import load_config, parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.runtime.switcher_executor import SwitcherCommandExecutor
from ptz_joystick_controller.state_machine.preview_program import PreviewProgramStateMachine
from ptz_joystick_controller.state_machine.ptz_control import PtzControlStateMachine
from ptz_joystick_controller.switchers.atem import AtemSwitcher
from ptz_joystick_controller.switchers.atem_production import (
    ATEM_MINI_PRO_DEFAULT_PORT,
    ATEM_MINI_PRO_SOURCE_TO_NATIVE,
    AtemMiniProClient,
)
from ptz_joystick_controller.switchers.capabilities import get_source_ids
from ptz_joystick_controller.switchers.factory import create_switcher, switcher_backend_name
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


class FakeAtemProtocol:
    def __init__(self) -> None:
        self.connected = False
        self.program = "Input 2"
        self.preview = "Input 1"
        self.transition_state = "idle"
        self.commands: list[tuple[str, str | None]] = []

    def connect(self) -> None: self.connected = True
    def disconnect(self) -> None: self.connected = False
    def poll(self): return self.program, self.preview
    def set_preview(self, source_id: str) -> None:
        self.commands.append(("preview", source_id))
        self.preview = source_id
    def cut(self) -> None:
        self.commands.append(("cut", None))
        self.program, self.preview = self.preview, self.program
    def auto(self) -> None:
        self.commands.append(("auto", None))
        self.program, self.preview = self.preview, self.program


def mini_config():
    return parse_config(
        {
            "switcher": {"type": "atem_mini_pro", "host": "192.0.2.50"},
            "ptz": {
                "cameras": [
                    {"id": f"cam{i}", "name": f"Cam {i}", "host": f"192.0.2.{100+i}", "enabled": True}
                    for i in range(1, 5)
                ]
            },
        }
    )


def test_mini_profile_exact_sources_and_native_ids() -> None:
    assert get_source_ids(SwitcherType.ATEM_MINI_PRO) == (
        "Input 1", "Input 2", "Input 3", "Input 4", "STILL", "BLACK"
    )
    assert ATEM_MINI_PRO_SOURCE_TO_NATIVE == {
        "Input 1": 1, "Input 2": 2, "Input 3": 3, "Input 4": 4, "STILL": 3010, "BLACK": 0
    }


def test_mini_default_port_and_factory_profile() -> None:
    assert ATEM_MINI_PRO_DEFAULT_PORT == 9910
    cfg = mini_config()
    fake = FakeAtemProtocol()
    switcher = create_switcher(cfg.switcher, offline=False, atem_client=fake)
    assert isinstance(switcher, AtemSwitcher)
    assert switcher.switcher_type == SwitcherType.ATEM_MINI_PRO
    assert switcher_backend_name(cfg.switcher) == "ATEM Mini Pro"


def test_mini_production_client_reuses_existing_manual_atem_transport() -> None:
    client = AtemMiniProClient("192.0.2.50")
    assert client._client.__class__.__name__ == "AtemManualControlClient"
    assert client.port == 9910


def test_preview_cut_auto_and_program_to_preview_use_generic_atem_interface() -> None:
    fake = FakeAtemProtocol()
    switcher = AtemSwitcher(SwitcherType.ATEM_MINI_PRO, fake)
    switcher.connect()

    switcher.set_preview_source("STILL")
    assert fake.commands[-1] == ("preview", "STILL")

    switcher.cut()
    assert fake.commands[-1] == ("cut", None)

    switcher.auto()
    assert fake.commands[-1] == ("auto", None)

    fake.program = "BLACK"
    switcher.poll()
    switcher.copy_program_to_preview()
    assert fake.commands[-1] == ("preview", "BLACK")


def test_prgi_prvi_confirmed_state_flows_through_shared_poll_contract() -> None:
    fake = FakeAtemProtocol()
    switcher = AtemSwitcher(SwitcherType.ATEM_MINI_PRO, fake)
    switcher.connect()
    fake.program = "Input 4"
    fake.preview = "Input 3"
    switcher.poll()
    assert switcher.get_program_source() == "Input 4"
    assert switcher.get_preview_source() == "Input 3"


def test_default_ptz_mapping_input_1_to_4_and_none_for_still_black() -> None:
    cfg = mini_config()
    for i in range(1, 5):
        assert cfg.ptz_camera_for_source(f"Input {i}") == f"cam{i}"
    assert cfg.ptz_camera_for_source("STILL") is None
    assert cfg.ptz_camera_for_source("BLACK") is None


def test_preview_feedback_routes_input_to_ptz_but_still_black_to_none() -> None:
    cfg = mini_config()
    fake = FakeAtemProtocol()
    switcher = AtemSwitcher(SwitcherType.ATEM_MINI_PRO, fake)
    switcher.connect()
    state = AppState(cfg)
    bus = EventBus()
    ptz = PtzControlStateMachine(state, bus)
    pp = PreviewProgramStateMachine(state, bus, ptz)
    executor = SwitcherCommandExecutor(switcher, state, bus, pp, ptz)

    for i in range(1, 5):
        fake.preview = f"Input {i}"
        executor.sync_from_switcher()
        assert state.active_ptz_camera_id == f"cam{i}"

    fake.preview = "STILL"
    executor.sync_from_switcher()
    assert state.active_ptz_camera_id is None
    fake.preview = "BLACK"
    executor.sync_from_switcher()
    assert state.active_ptz_camera_id is None


def test_loading_generic_example_as_mini_fills_profile_defaults(tmp_path: Path) -> None:
    base = tmp_path / "config.example.yaml"
    base.write_text(Path("config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    local = tmp_path / "config.local.yaml"
    local.write_text("switcher:\n  type: atem_mini_pro\n  host: 192.168.1.181\n", encoding="utf-8")
    cfg = load_config(base, local_path=local)
    for i in range(1, 5):
        assert cfg.ptz_camera_for_source(f"Input {i}") == f"cam{i}"
    assert cfg.ptz_camera_for_source("STILL") is None
    assert cfg.ptz_camera_for_source("BLACK") is None


def test_config_ui_selectable_exact_sources_and_default_port(tmp_path: Path) -> None:
    cfg = mini_config()
    app = create_web_app(
        RuntimeStatusProvider(AppState(cfg)),
        config_example_path=tmp_path / "config.example.yaml",
        config_local_path=tmp_path / "config.local.yaml",
    )
    html = TestClient(app).get("/config").text
    assert '<option value="atem_mini_pro" selected>ATEM Mini Pro</option>' in html
    for source in ("Input 1", "Input 2", "Input 3", "Input 4", "STILL", "BLACK"):
        assert f'value="{source}"' in html
    assert "type === 'atem_mini_pro'" in html
    assert "'9910'" in html


def test_existing_4k8_profile_is_unchanged() -> None:
    assert get_source_ids(SwitcherType.ATEM_TELEVISION_STUDIO_4K8) == (
        "Input 1", "Input 2", "Input 3", "Input 4", "Input 5", "Input 6", "Input 7", "Input 8",
        "Black", "MP1", "MP2", "SuperSource",
    )


def test_discovery_remains_read_only_for_atem() -> None:
    source = Path("src/ptz_joystick_controller/discovery/network_probe.py").read_text(encoding="utf-8")
    assert "CPvI" not in source
    assert "DCut" not in source
    assert "DAut" not in source

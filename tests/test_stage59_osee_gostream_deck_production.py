from __future__ import annotations

from collections import deque
from pathlib import Path

from fastapi.testclient import TestClient

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import load_config, parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.runtime.switcher_executor import SwitcherCommandExecutor
from ptz_joystick_controller.state_machine.preview_program import PreviewProgramStateMachine
from ptz_joystick_controller.state_machine.ptz_control import PtzControlStateMachine
from ptz_joystick_controller.switchers.capabilities import get_source_ids
from ptz_joystick_controller.switchers.factory import create_switcher, switcher_backend_name
from ptz_joystick_controller.switchers.osee_deck_gsp import OseeDeckSourceMap
from ptz_joystick_controller.switchers.osee_gostream_deck import OseeGoStreamDeckSwitcher
from ptz_joystick_controller.switchers.osee_gostream_duet import OseeGoStreamDuetSwitcher
from ptz_joystick_controller.switchers.osee_gsp import GspCommand
from ptz_joystick_controller.switchers.vmix import VmixSwitcher
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


class FakeGspTransport:
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
        return b"x"

    def send_get(self, command_id: str) -> bytes:
        return self.send_command(GspCommand(id=command_id, type="get"))

    def receive(self) -> tuple[GspCommand, ...]:
        return self.incoming.popleft() if self.incoming else ()


def deck_config():
    return parse_config(
        {
            "switcher": {"type": "osee_gostream_deck", "host": "192.0.2.40"},
            "ptz": {
                "cameras": [
                    {"id": "cam1", "name": "Cam 1", "host": "192.0.2.101", "enabled": True},
                    {"id": "cam2", "name": "Cam 2", "host": "192.0.2.102", "enabled": True},
                    {"id": "cam3", "name": "Cam 3", "host": "192.0.2.103", "enabled": True},
                    {"id": "cam4", "name": "Cam 4", "host": "192.0.2.104", "enabled": True},
                ]
            },
        }
    )


def test_deck_capability_model_is_exact() -> None:
    assert get_source_ids(SwitcherType.OSEE_GOSTREAM_DECK) == (
        "Input 1",
        "Input 2",
        "Input 3",
        "Input 4",
        "AUX",
        "STILL1",
        "STILL2",
        "S/SRC",
    )


def test_deck_mapping_normalizes_both_directions() -> None:
    expected = {
        "Input 1": 1,
        "Input 2": 2,
        "Input 3": 3,
        "Input 4": 4,
        "AUX": 4001,
        "STILL1": 3010,
        "STILL2": 3020,
        "S/SRC": 5001,
    }
    for logical, native in expected.items():
        assert OseeDeckSourceMap.to_gsp_id(logical) == native
        assert OseeDeckSourceMap.from_gsp_id(native).canonical_id == logical


def test_factory_builds_production_deck_gsp_backend_default_port() -> None:
    transport = FakeGspTransport()
    cfg = deck_config()
    switcher = create_switcher(
        cfg.switcher,
        offline=False,
        osee_transport_factory=lambda _host, _port: transport,
    )
    assert isinstance(switcher, OseeGoStreamDeckSwitcher)
    assert switcher.port == 19010
    assert switcher_backend_name(cfg.switcher) == "Osee GoStream Deck"


def test_deck_preview_commands_use_verified_ids_for_every_source() -> None:
    transport = FakeGspTransport()
    switcher = create_switcher(
        deck_config().switcher,
        offline=False,
        osee_transport_factory=lambda _host, _port: transport,
    )
    switcher.connect()
    transport.sent.clear()
    for logical, native in (
        ("Input 1", 1),
        ("Input 2", 2),
        ("Input 3", 3),
        ("Input 4", 4),
        ("AUX", 4001),
        ("STILL1", 3010),
        ("STILL2", 3020),
        ("S/SRC", 5001),
    ):
        switcher.set_preview_source(logical)
        assert transport.sent[-1] == GspCommand(id="pvwIndex", type="set", value=(native,))
        assert switcher.get_preview_source() == logical


def test_deck_cut_auto_and_program_to_preview_use_native_gsp() -> None:
    transport = FakeGspTransport()
    switcher = create_switcher(
        deck_config().switcher,
        offline=False,
        osee_transport_factory=lambda _host, _port: transport,
    )
    switcher.connect()
    transport.sent.clear()

    switcher.cut()
    assert transport.sent[-1] == GspCommand(id="cutTransition", type="set")

    switcher.auto()
    assert transport.sent[-1] == GspCommand(id="autoTransition", type="set")

    transport.incoming.append((GspCommand(id="pgmIndex", type="pus", value=(3010,)),))
    switcher.poll()
    assert switcher.get_program_source() == "STILL1"
    switcher.copy_program_to_preview()
    assert transport.sent[-1] == GspCommand(id="pvwIndex", type="set", value=(3010,))


def test_physical_feedback_updates_program_preview_and_transition() -> None:
    transport = FakeGspTransport()
    switcher = create_switcher(
        deck_config().switcher,
        offline=False,
        osee_transport_factory=lambda _host, _port: transport,
    )
    switcher.connect()
    transport.incoming.append(
        (
            GspCommand(id="pgmIndex", type="pus", value=(4001,)),
            GspCommand(id="pvwIndex", type="pus", value=(5001,)),
            GspCommand(id="transitionStatus", type="pus", value=(1,)),
        )
    )
    switcher.poll()
    assert switcher.get_program_source() == "AUX"
    assert switcher.get_preview_source() == "S/SRC"
    assert switcher.transition_state == (1,)


def test_generic_runtime_state_and_ptz_follow_deck_preview_feedback() -> None:
    cfg = deck_config()
    transport = FakeGspTransport()
    switcher = create_switcher(
        cfg.switcher,
        offline=False,
        osee_transport_factory=lambda _host, _port: transport,
    )
    switcher.connect()
    bus = EventBus()
    state = AppState(cfg)
    ptz = PtzControlStateMachine(state, bus)
    preview_program = PreviewProgramStateMachine(state, bus, ptz)
    executor = SwitcherCommandExecutor(switcher, state, bus, preview_program, ptz)

    transport.incoming.append(
        (
            GspCommand(id="pgmIndex", type="pus", value=(2,)),
            GspCommand(id="pvwIndex", type="pus", value=(3,)),
            GspCommand(id="transitionStatus", type="pus", value=(0,)),
        )
    )
    executor.sync_from_switcher()
    assert state.program_source_id == "Input 2"
    assert state.preview_source_id == "Input 3"
    assert state.active_ptz_camera_id == "cam3"


def test_deck_default_ptz_mapping_and_special_sources() -> None:
    cfg = deck_config()
    for index in range(1, 5):
        assert cfg.ptz_camera_for_source(f"Input {index}") == f"cam{index}"
    assert cfg.ptz_camera_for_source("AUX") is None
    assert cfg.ptz_camera_for_source("STILL1") is None
    assert cfg.ptz_camera_for_source("STILL2") is None
    assert cfg.ptz_camera_for_source("S/SRC") is None


def test_deck_aux_can_be_mapped_to_any_existing_camera_or_none() -> None:
    cfg = parse_config(
        {
            "switcher": {"type": "osee_gostream_deck", "host": "x"},
            "ptz": {
                "cameras": [
                    {"id": "cam1", "name": "Cam 1", "enabled": False},
                    {"id": "cam2", "name": "Cam 2", "enabled": False},
                ]
            },
            "sources": {
                "mappings": [
                    {"source_id": "AUX", "display_name": "AUX", "ptz_camera_id": "cam2"}
                ]
            },
        }
    )
    assert cfg.ptz_camera_for_source("AUX") == "cam2"


def test_loading_generic_example_as_deck_fills_input_1_to_4_defaults_but_preserves_local_none(tmp_path: Path) -> None:
    base = tmp_path / "config.example.yaml"
    base.write_text(Path("config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    local = tmp_path / "config.local.yaml"
    local.write_text(
        "switcher:\n  type: osee_gostream_deck\n  host: 192.168.1.182\n"
        "sources:\n  mappings:\n    - source_id: Input 3\n      ptz_camera_id: null\n",
        encoding="utf-8",
    )
    cfg = load_config(base, local_path=local)
    assert cfg.ptz_camera_for_source("Input 1") == "cam1"
    assert cfg.ptz_camera_for_source("Input 2") == "cam2"
    assert cfg.ptz_camera_for_source("Input 3") is None
    assert cfg.ptz_camera_for_source("Input 4") == "cam4"
    assert cfg.ptz_camera_for_source("AUX") is None


def test_config_gui_offers_deck_and_exact_source_dropdown_and_aux_ptz_mapping(tmp_path: Path) -> None:
    cfg = deck_config()
    provider = RuntimeStatusProvider(AppState(cfg))
    app = create_web_app(
        provider,
        config_example_path=tmp_path / "config.example.yaml",
        config_local_path=tmp_path / "config.local.yaml",
    )
    html = TestClient(app).get("/config").text
    assert '<option value="osee_gostream_deck" selected>Osee GoStream Deck</option>' in html
    for source in ("Input 1", "Input 2", "Input 3", "Input 4", "AUX", "STILL1", "STILL2", "S/SRC"):
        assert f'value="{source}"' in html
    assert "GoStream Deck additional PTZ source mappings" in html
    assert "source_mapping_AUX" in html
    assert "AUX may be mapped to any configured PTZ camera or None" in html
    assert "type === 'osee_gostream_deck'" in html
    assert "'19010'" in html


def test_osee_discovery_code_remains_read_only() -> None:
    source = Path("src/ptz_joystick_controller/discovery/network_probe.py").read_text(encoding="utf-8")
    start = source.index("def probe_osee")
    end = source.index("VISCA_VERSION_INQUIRY_PAYLOAD", start)
    probe = source[start:end]
    assert 'send_get(command_id)' in probe
    assert 'send_set(' not in probe
    assert '"type": "set"' not in probe


def test_regression_existing_switcher_source_profiles_unchanged() -> None:
    assert get_source_ids("osee_gostream_duet") == (
        "Input 1", "Input 2", "Input 3", "Input 4",
        "Input 5", "Input 6", "Input 7", "Input 8",
        "MP1", "MP2", "M/SRC",
    )
    assert get_source_ids("vmix")[0] == "Input 1"
    assert get_source_ids("vmix")[-1] == "Input 100"
    assert get_source_ids("atem_television_studio_4k8")[:8] == tuple(f"Input {i}" for i in range(1, 9))


def test_regression_factory_types_remain_separate() -> None:
    duet_transport = FakeGspTransport()
    duet_cfg = parse_config({"switcher": {"type": "osee_gostream_duet", "host": "x"}})
    duet = create_switcher(
        duet_cfg.switcher,
        offline=False,
        osee_transport_factory=lambda _h, _p: duet_transport,
    )
    assert isinstance(duet, OseeGoStreamDuetSwitcher)

    vmix_cfg = parse_config({"switcher": {"type": "vmix", "host": "127.0.0.1"}})
    assert isinstance(create_switcher(vmix_cfg.switcher, offline=False), VmixSwitcher)

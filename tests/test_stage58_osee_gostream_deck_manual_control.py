from __future__ import annotations

import importlib.util
from pathlib import Path
import queue
import threading
import time

import pytest

from ptz_joystick_controller.switchers.osee_deck_gsp import (
    DECK_GSP_ID_TO_SOURCE,
    DECK_SOURCE_TO_GSP_ID,
    OseeDeckGspController,
    OseeDeckManualControlClient,
    OseeDeckSourceError,
    OseeDeckSourceMap,
)
from ptz_joystick_controller.switchers.osee_duet_gsp import DUET_SOURCE_TO_GSP_ID
from ptz_joystick_controller.switchers.osee_gsp import (
    GspCommand,
    GspTransportError,
    decode_gsp_packet,
    encode_gsp_command,
)


class FakeDeckTransport:
    def __init__(self) -> None:
        self.connected = False
        self.commands: list[GspCommand] = []
        self.get_ids: list[str] = []
        self.receive_calls = 0
        self.receive_thread_ids: set[int] = set()
        self.disconnect_calls = 0
        self._queue: queue.Queue[GspCommand | None] = queue.Queue()

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False
        self._queue.put(None)

    def send_command(self, command: GspCommand) -> bytes:
        if not self.connected:
            raise GspTransportError("not connected")
        self.commands.append(command)
        return encode_gsp_command(command)

    def send_get(self, command_id: str) -> bytes:
        self.get_ids.append(command_id)
        return self.send_command(GspCommand(id=command_id, type="get"))

    def receive(self) -> tuple[GspCommand, ...]:
        self.receive_calls += 1
        self.receive_thread_ids.add(threading.get_ident())
        try:
            item = self._queue.get(timeout=0.02)
        except queue.Empty:
            return ()
        if item is None:
            return ()
        return (item,)

    def push(self, command: GspCommand) -> None:
        self._queue.put(command)


def wait_until(predicate, timeout: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def push_later(transport: FakeDeckTransport, command: GspCommand, delay: float = 0.02) -> threading.Timer:
    timer = threading.Timer(delay, lambda: transport.push(command))
    timer.daemon = True
    timer.start()
    return timer


def test_deck_source_mapping_is_exact_and_distinct_from_duet() -> None:
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
    assert DECK_SOURCE_TO_GSP_ID == expected
    assert DECK_GSP_ID_TO_SOURCE == {value: key for key, value in expected.items()}
    assert DUET_SOURCE_TO_GSP_ID[ "Input 5"] == 4001
    assert "Input 5" not in DECK_SOURCE_TO_GSP_ID
    assert DECK_GSP_ID_TO_SOURCE[4001] == "AUX"
    assert DECK_GSP_ID_TO_SOURCE[5001] == "S/SRC"


@pytest.mark.parametrize(
    ("alias", "canonical", "gsp_id"),
    [
        ("input1", "Input 1", 1),
        ("Input 2", "Input 2", 2),
        ("3", "Input 3", 3),
        ("input4", "Input 4", 4),
        ("aux", "AUX", 4001),
        ("still1", "STILL1", 3010),
        ("still2", "STILL2", 3020),
        ("ssrc", "S/SRC", 5001),
        ("S/SRC", "S/SRC", 5001),
        ("MultiSource", "S/SRC", 5001),
    ],
)
def test_deck_aliases_map_both_directions(alias: str, canonical: str, gsp_id: int) -> None:
    assert OseeDeckSourceMap.normalize(alias) == canonical
    assert OseeDeckSourceMap.to_gsp_id(alias) == gsp_id
    ref = OseeDeckSourceMap.from_gsp_id(gsp_id)
    assert ref.canonical_id == canonical
    assert ref.display_name == canonical


@pytest.mark.parametrize("bad", ["", "5", "input5", "M/SRC", "mp1", "foo"])
def test_invalid_or_duet_only_source_rejected(bad: str) -> None:
    with pytest.raises(OseeDeckSourceError):
        OseeDeckSourceMap.to_gsp_id(bad)


@pytest.mark.parametrize(
    ("source", "expected_id"),
    [
        ("Input 1", 1), ("Input 2", 2), ("Input 3", 3), ("Input 4", 4),
        ("AUX", 4001), ("STILL1", 3010), ("STILL2", 3020), ("S/SRC", 5001),
    ],
)
def test_preview_command_uses_correct_deck_id(source: str, expected_id: int) -> None:
    transport = FakeDeckTransport()
    transport.connected = True
    packet = OseeDeckGspController(transport).set_preview(source)
    assert decode_gsp_packet(packet) == GspCommand(id="pvwIndex", type="set", value=(expected_id,))


def test_cut_uses_native_gsp_operation() -> None:
    transport = FakeDeckTransport()
    transport.connected = True
    command = decode_gsp_packet(OseeDeckGspController(transport).cut())
    assert command == GspCommand(id="cutTransition", type="set", value=None)


def test_auto_uses_native_gsp_operation() -> None:
    transport = FakeDeckTransport()
    transport.connected = True
    command = decode_gsp_packet(OseeDeckGspController(transport).auto())
    assert command == GspCommand(id="autoTransition", type="set", value=None)


def test_preview_success_requires_matching_pvw_feedback() -> None:
    transport = FakeDeckTransport()
    client = OseeDeckManualControlClient(transport, feedback_timeout=0.25)
    client.connect()
    try:
        push_later(transport, GspCommand(id="pvwIndex", type="pus", value=(3,)))
        assert client.set_preview("Input 3") is True
        assert client.snapshot().preview == "Input 3"
    finally:
        client.disconnect()


def test_preview_wrong_feedback_does_not_claim_success() -> None:
    transport = FakeDeckTransport()
    client = OseeDeckManualControlClient(transport, feedback_timeout=0.08)
    client.connect()
    try:
        push_later(transport, GspCommand(id="pvwIndex", type="pus", value=(2,)))
        assert client.set_preview("Input 3") is False
        assert client.snapshot().preview == "Input 2"
    finally:
        client.disconnect()


def test_preview_timeout_is_safe() -> None:
    transport = FakeDeckTransport()
    client = OseeDeckManualControlClient(transport, feedback_timeout=0.04)
    client.connect()
    try:
        assert client.set_preview("AUX") is False
        assert GspCommand(id="pvwIndex", type="set", value=(4001,)) in transport.commands
    finally:
        client.disconnect()


def test_cut_state_is_changed_only_by_real_feedback() -> None:
    transport = FakeDeckTransport()
    client = OseeDeckManualControlClient(transport, feedback_timeout=0.2)
    client.connect()
    try:
        transport.push(GspCommand(id="pgmIndex", type="pus", value=(1,)))
        transport.push(GspCommand(id="pvwIndex", type="pus", value=(2,)))
        assert wait_until(lambda: client.snapshot().program == "Input 1" and client.snapshot().preview == "Input 2")
        push_later(transport, GspCommand(id="pgmIndex", type="pus", value=(2,)))
        push_later(transport, GspCommand(id="pvwIndex", type="pus", value=(1,)), 0.03)
        assert client.cut() is True
        assert wait_until(lambda: client.snapshot().program == "Input 2" and client.snapshot().preview == "Input 1")
        assert GspCommand(id="cutTransition", type="set") in transport.commands
    finally:
        client.disconnect()


def test_auto_uses_transition_status_started_and_completed_feedback() -> None:
    transport = FakeDeckTransport()
    client = OseeDeckManualControlClient(transport, feedback_timeout=0.25)
    client.connect()
    try:
        push_later(transport, GspCommand(id="transitionStatus", type="pus", value=(1,)), 0.02)
        push_later(transport, GspCommand(id="transitionStatus", type="pus", value=(0,)), 0.05)
        assert client.auto() == (True, True)
        assert client.snapshot().transition == "idle"
        assert GspCommand(id="autoTransition", type="set") in transport.commands
    finally:
        client.disconnect()


@pytest.mark.parametrize(
    ("program_id", "program_name"),
    [(1, "Input 1"), (2, "Input 2"), (3, "Input 3"), (4, "Input 4"), (3010, "STILL1")],
)
def test_copy_program_to_preview_uses_real_program_mapping(program_id: int, program_name: str) -> None:
    transport = FakeDeckTransport()
    client = OseeDeckManualControlClient(transport, feedback_timeout=0.25)
    client.connect()
    try:
        transport.push(GspCommand(id="pgmIndex", type="pus", value=(program_id,)))
        assert wait_until(lambda: client.snapshot().program == program_name)
        push_later(transport, GspCommand(id="pvwIndex", type="pus", value=(program_id,)))
        source, confirmed = client.copy_program_to_preview()
        assert source == program_name
        assert confirmed is True
        expected = GspCommand(id="pvwIndex", type="set", value=(program_id,))
        assert expected in transport.commands
    finally:
        client.disconnect()


def test_physical_program_preview_changes_update_state_while_idle() -> None:
    transport = FakeDeckTransport()
    client = OseeDeckManualControlClient(transport, feedback_timeout=0.2)
    client.connect()
    try:
        transport.push(GspCommand(id="pgmIndex", type="pus", value=(3010,)))
        transport.push(GspCommand(id="pvwIndex", type="pus", value=(5001,)))
        assert wait_until(
            lambda: client.snapshot().program == "STILL1" and client.snapshot().preview == "S/SRC"
        )
        assert client.receiver_alive
    finally:
        client.disconnect()


def test_exactly_one_background_component_reads_transport() -> None:
    transport = FakeDeckTransport()
    client = OseeDeckManualControlClient(transport, feedback_timeout=0.1)
    client.connect()
    try:
        assert wait_until(lambda: transport.receive_calls >= 2)
        client.snapshot()
        time.sleep(0.03)
        assert len(transport.receive_thread_ids) == 1
        assert threading.get_ident() not in transport.receive_thread_ids
    finally:
        client.disconnect()


def test_malformed_or_unexpected_messages_do_not_crash_controller() -> None:
    transport = FakeDeckTransport()
    transport.connected = True
    controller = OseeDeckGspController(transport)
    assert controller.handle_command(GspCommand(id="pvwIndex", type="pus", value=())) is False
    assert controller.handle_command(GspCommand(id="unknownCommand", type="pus", value=(1,))) is False
    assert controller.handle_command(GspCommand(id="pgmIndex", type="set", value=(1,))) is False


def test_connect_requests_authoritative_initial_state() -> None:
    transport = FakeDeckTransport()
    client = OseeDeckManualControlClient(transport, feedback_timeout=0.1)
    client.connect()
    try:
        assert transport.get_ids == ["pgmIndex", "pvwIndex", "transitionStatus"]
    finally:
        client.disconnect()


def test_clean_disconnect_stops_receiver() -> None:
    transport = FakeDeckTransport()
    client = OseeDeckManualControlClient(transport, feedback_timeout=0.1)
    client.connect()
    assert client.receiver_alive
    client.disconnect()
    assert not client.receiver_alive
    assert transport.connected is False
    assert transport.disconnect_calls == 1


def test_ctrl_c_cleanup_in_manual_script(monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path("scripts/manual_osee_gostream_deck_control.py")
    spec = importlib.util.spec_from_file_location("manual_osee_deck_stage58", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    events: list[str] = []

    class FakeTransportForScript:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class FakeClientForScript:
        def __init__(self, transport, *, feedback_timeout) -> None:
            pass
        def connect(self) -> None:
            events.append("connect")
        def wait_for_initial_state(self, timeout) -> bool:
            return False
        def snapshot(self):
            class State:
                program = None
                preview = None
                transition = "unknown"
            return State()
        def disconnect(self) -> None:
            events.append("disconnect")

    monkeypatch.setattr(module, "OseeGspTransport", FakeTransportForScript)
    monkeypatch.setattr(module, "OseeDeckManualControlClient", FakeClientForScript)
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr("sys.argv", ["manual_osee_gostream_deck_control.py", "--host", "192.168.1.182"])
    assert module.main() == 0
    assert events == ["connect", "disconnect"]

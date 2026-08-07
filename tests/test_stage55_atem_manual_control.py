from __future__ import annotations

import socket

import pytest

from ptz_joystick_controller.switchers.atem_control_probe import (
    ATEM_ME1_INDEX,
    ATEM_TELEVISION_STUDIO_4K8_PRODUCT_NAME,
    AtemCommandTimeout,
    AtemStateFeedbackTimeout,
    AtemTransportAckTimeout,
    AtemManualControlClient,
    auto_command,
    cut_command,
    is_stage55_control_command,
    preview_input_command,
)
from ptz_joystick_controller.switchers.atem_probe import (
    ATEM_FLAG_ACK,
    ATEM_FLAG_RELIABLE,
    ATEM_FLAG_SYN,
    ATEM_INITIAL_SESSION_ID,
    decode_atem_packet,
    encode_atem_packet,
    encode_readonly_state_command,
    parse_atem_commands,
)


def cmd(name: str, payload: bytes) -> bytes:
    return encode_readonly_state_command(name, payload)


def pin_payload(name: str = ATEM_TELEVISION_STUDIO_4K8_PRODUCT_NAME, model: int = 42) -> bytes:
    encoded = name.encode()[:39]
    return encoded + b"\x00" * (40 - len(encoded)) + bytes([model, 0, 0, 0])


def input_payload(source_id: int, long_name: str, short_name: str) -> bytes:
    long = long_name.encode()[:19]
    short = short_name.encode()[:4]
    return (
        source_id.to_bytes(2, "big")
        + long + b"\x00" * (20 - len(long))
        + short + b"\x00" * (4 - len(short))
        + b"\x00" * 10
    )


class FakeUdpSocket:
    def __init__(self, receives: list[bytes | BaseException]) -> None:
        self.receives = list(receives)
        self.sent: list[bytes] = []
        self.timeout: float | None = None
        self.connected_to: tuple[str, int] | None = None
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def connect(self, address: tuple[str, int]) -> None:
        self.connected_to = address

    def send(self, data: bytes) -> int:
        self.sent.append(data)
        return len(data)

    def recv(self, size: int) -> bytes:
        if not self.receives:
            raise socket.timeout()
        value = self.receives.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


def handshake_reply() -> bytes:
    return encode_atem_packet(
        flags=ATEM_FLAG_SYN,
        session_id=ATEM_INITIAL_SESSION_ID,
        packet_id=1,
        payload=b"\x02\x00\x00\x00\x00\x00\x00\x00",
    )




def ack_datagram(ack_id: int, *, session_id: int = 0x800F) -> bytes:
    return encode_atem_packet(
        flags=ATEM_FLAG_ACK,
        session_id=session_id,
        ack_id=ack_id,
    )


def state_datagram(*commands: bytes, packet_id: int = 2, session_id: int = 0x800F) -> bytes:
    return encode_atem_packet(
        flags=ATEM_FLAG_RELIABLE,
        session_id=session_id,
        packet_id=packet_id,
        payload=b"".join(commands),
    )


def initial_state_datagram(*, program: int = 1, preview: int = 2) -> bytes:
    commands = [
        cmd("_ver", b"\x00\x02\x00\x20"),
        cmd("_pin", pin_payload()),
    ]
    for source_id in range(1, 9):
        commands.append(cmd("InPr", input_payload(source_id, f"Camera {source_id}", f"C{source_id}")))
    commands.extend(
        [
            cmd("PrgI", bytes((0, 0)) + program.to_bytes(2, "big")),
            cmd("PrvI", bytes((0, 0)) + preview.to_bytes(2, "big") + b"\x00\x00\x00\x00"),
            cmd("InCm", b""),
        ]
    )
    return state_datagram(*commands)


def sent_control_commands(fake: FakeUdpSocket) -> list[tuple[str, bytes]]:
    result: list[tuple[str, bytes]] = []
    for raw in fake.sent:
        packet = decode_atem_packet(raw)
        if packet.flags != ATEM_FLAG_RELIABLE or not packet.payload:
            continue
        for command in parse_atem_commands(packet.payload):
            result.append((command.name, command.payload))
    return result


def test_preview_command_packet_encoding_and_source_id() -> None:
    command = preview_input_command(5)
    assert command.name == "CPvI"
    assert command.payload == b"\x00\x00\x00\x05"
    assert command.encode_chunk() == bytes.fromhex("00 0c 00 00 43 50 76 49 00 00 00 05")


def test_preview_command_supports_arbitrary_u16_source_id() -> None:
    command = preview_input_command(1000)
    assert command.payload[0] == ATEM_ME1_INDEX
    assert int.from_bytes(command.payload[2:4], "big") == 1000


def test_cut_command_packet_encoding_and_me_index() -> None:
    command = cut_command()
    assert command.name == "DCut"
    assert command.payload == b"\x00\x00\x00\x00"
    assert command.encode_chunk() == bytes.fromhex("00 0c 00 00 44 43 75 74 00 00 00 00")


def test_auto_command_packet_encoding_and_me_index() -> None:
    command = auto_command()
    assert command.name == "DAut"
    assert command.payload == b"\x00\x00\x00\x00"
    assert command.encode_chunk() == bytes.fromhex("00 0c 00 00 44 41 75 74 00 00 00 00")


def test_non_me1_command_is_rejected() -> None:
    with pytest.raises(ValueError, match="M/E 1 only"):
        preview_input_command(1, me_index=1)
    with pytest.raises(ValueError, match="M/E 1 only"):
        cut_command(me_index=1)
    with pytest.raises(ValueError, match="M/E 1 only"):
        auto_command(me_index=1)


def test_invalid_source_id_rejected() -> None:
    for value in (-1, 65536):
        with pytest.raises(ValueError):
            preview_input_command(value)
    with pytest.raises(TypeError):
        preview_input_command(True)  # type: ignore[arg-type]


def test_set_preview_sends_cpvi_and_waits_for_prvi_feedback() -> None:
    fake = FakeUdpSocket([
        handshake_reply(),
        initial_state_datagram(),
        ack_datagram(1),
        state_datagram(cmd("PrvI", b"\x00\x00\x00\x05\x00\x00\x00\x00"), packet_id=3),
    ])
    client = AtemManualControlClient("192.168.1.184", socket_factory=lambda: fake)
    client.connect()
    program_before = client.state.program_source_id
    client.set_preview(5)
    assert client.state.preview_source_id == 5
    assert client.state.program_source_id == program_before
    assert sent_control_commands(fake)[-1] == ("CPvI", b"\x00\x00\x00\x05")


def test_set_preview_rejects_unknown_source_when_input_table_available() -> None:
    fake = FakeUdpSocket([handshake_reply(), initial_state_datagram()])
    client = AtemManualControlClient("192.168.1.184", socket_factory=lambda: fake)
    client.connect()
    with pytest.raises(ValueError, match="not present"):
        client.set_preview(999)
    assert sent_control_commands(fake) == []


def test_cut_relies_on_received_program_preview_feedback() -> None:
    fake = FakeUdpSocket([
        handshake_reply(),
        initial_state_datagram(program=1, preview=2),
        ack_datagram(1),
        state_datagram(
            cmd("PrgI", b"\x00\x00\x00\x02"),
            cmd("PrvI", b"\x00\x00\x00\x01\x00\x00\x00\x00"),
            packet_id=3,
        ),
    ])
    client = AtemManualControlClient("192.168.1.184", socket_factory=lambda: fake)
    client.connect()
    client.cut()
    assert client.state.program_source_id == 2
    assert client.state.preview_source_id == 1
    assert sent_control_commands(fake)[-1][0] == "DCut"


def test_auto_relies_on_received_feedback() -> None:
    fake = FakeUdpSocket([
        handshake_reply(),
        initial_state_datagram(program=1, preview=2),
        ack_datagram(1),
        state_datagram(cmd("TrPs", b"\x00\x01\x0a\x00\x13\x88\x00"), packet_id=3),
        state_datagram(
            cmd("TrPs", b"\x00\x00\x00\x00\x00\x00\x00"),
            cmd("PrgI", b"\x00\x00\x00\x02"),
            cmd("PrvI", b"\x00\x00\x00\x01\x00\x00\x00\x00"),
            packet_id=4,
        ),
    ])
    client = AtemManualControlClient("192.168.1.184", socket_factory=lambda: fake)
    client.connect()
    client.auto()
    assert client.state.program_source_id == 2
    assert client.state.preview_source_id == 1
    assert client.state.transition_in_progress is False
    assert sent_control_commands(fake)[-1][0] == "DAut"


def test_timeout_after_command_is_safe_and_keeps_session_alive() -> None:
    fake = FakeUdpSocket([handshake_reply(), initial_state_datagram(), ack_datagram(1), socket.timeout(), socket.timeout()])
    client = AtemManualControlClient("192.168.1.184", timeout=0.01, socket_factory=lambda: fake)
    client.connect()
    with pytest.raises(AtemStateFeedbackTimeout):
        client.set_preview(5, feedback_timeout=0.01)
    assert client.connected is True
    assert client.confirmed is True
    assert client.state.preview_source_id == 2


def test_stage55_generates_only_preview_cut_auto_high_level_commands() -> None:
    commands = [preview_input_command(1), cut_command(), auto_command()]
    assert all(is_stage55_control_command(command) for command in commands)
    assert {command.name for command in commands} == {"CPvI", "DCut", "DAut"}


def test_exact_product_name_constant() -> None:
    assert ATEM_TELEVISION_STUDIO_4K8_PRODUCT_NAME == "ATEM Television Studio 4K8"


def test_clean_disconnect_after_control_session() -> None:
    fake = FakeUdpSocket([handshake_reply(), initial_state_datagram()])
    client = AtemManualControlClient("192.168.1.184", socket_factory=lambda: fake)
    client.connect()
    client.disconnect()
    assert fake.closed is True
    assert client.connected is False


def test_outbound_packet_ids_are_monotonic_within_session() -> None:
    fake = FakeUdpSocket([
        handshake_reply(),
        initial_state_datagram(),
        ack_datagram(1),
        state_datagram(cmd("PrvI", b"\x00\x00\x00\x03\x00\x00\x00\x00"), packet_id=3),
        ack_datagram(2),
        state_datagram(cmd("PrvI", b"\x00\x00\x00\x04\x00\x00\x00\x00"), packet_id=4),
    ])
    client = AtemManualControlClient("192.168.1.184", socket_factory=lambda: fake)
    client.connect()
    client.set_preview(3)
    client.set_preview(4)
    command_packets = [decode_atem_packet(raw) for raw in fake.sent if decode_atem_packet(raw).flags & ATEM_FLAG_RELIABLE]
    assert [packet.packet_id for packet in command_packets] == [1, 2]


def test_reconnect_resets_session_local_sequence() -> None:
    first = FakeUdpSocket([handshake_reply(), initial_state_datagram(), ack_datagram(1), state_datagram(cmd("PrvI", b"\x00\x00\x00\x03\x00\x00\x00\x00"), packet_id=3)])
    second = FakeUdpSocket([handshake_reply(), initial_state_datagram(), ack_datagram(1), state_datagram(cmd("PrvI", b"\x00\x00\x00\x03\x00\x00\x00\x00"), packet_id=3)])
    sockets = iter([first, second])
    client = AtemManualControlClient("192.168.1.184", socket_factory=lambda: next(sockets))
    client.connect(); client.set_preview(3)
    client.connect(); client.set_preview(3)
    first_ids = [decode_atem_packet(raw).packet_id for raw in first.sent if decode_atem_packet(raw).flags & ATEM_FLAG_RELIABLE]
    second_ids = [decode_atem_packet(raw).packet_id for raw in second.sent if decode_atem_packet(raw).flags & ATEM_FLAG_RELIABLE]
    assert first_ids == [1]
    assert second_ids == [1]


def test_switcher_ack_packet_is_correlated_and_not_parsed_as_state() -> None:
    fake = FakeUdpSocket([handshake_reply(), initial_state_datagram(), ack_datagram(1)])
    client = AtemManualControlClient("192.168.1.184", socket_factory=lambda: fake)
    client.connect()
    packet_id = client._send_control(preview_input_command(3))
    commands = client.receive_once()
    assert commands == ()
    assert client.is_local_packet_acked(packet_id) is True
    assert client.consume_local_packet_ack(packet_id) is True
    assert client.is_local_packet_acked(packet_id) is False


def test_transport_ack_timeout_is_distinct_from_state_feedback_timeout() -> None:
    fake = FakeUdpSocket([handshake_reply(), initial_state_datagram(), socket.timeout(), socket.timeout()])
    client = AtemManualControlClient("192.168.1.184", timeout=0.01, socket_factory=lambda: fake)
    client.connect()
    with pytest.raises(AtemTransportAckTimeout):
        client.set_preview(3, feedback_timeout=0.01)
    assert client.last_command_transport_acked is False


def test_cpvi_payload_regression_source_1_and_2() -> None:
    assert preview_input_command(1).payload == bytes.fromhex("00 00 00 01")
    assert preview_input_command(2).payload == bytes.fromhex("00 00 00 02")

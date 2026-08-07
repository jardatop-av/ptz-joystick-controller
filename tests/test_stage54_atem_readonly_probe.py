from __future__ import annotations

import socket

import pytest

from ptz_joystick_controller.switchers.atem_probe import (
    ATEM_CONNECT_PAYLOAD,
    ATEM_FLAG_ACK,
    ATEM_FLAG_RELIABLE,
    ATEM_FLAG_SYN,
    ATEM_INITIAL_SESSION_ID,
    AtemProtocolError,
    AtemReadOnlyProbeClient,
    AtemTimeoutError,
    decode_atem_packet,
    encode_ack,
    encode_atem_packet,
    encode_connect_hello,
    encode_readonly_state_command,
    is_readonly_session_packet,
    parse_atem_commands,
)


def cmd(name: str, payload: bytes) -> bytes:
    return encode_readonly_state_command(name, payload)


def pin_payload(name: str = "ATEM Television Studio Pro 4K", model: int = 10) -> bytes:
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


def state_datagram(*commands: bytes, packet_id: int = 2, session_id: int = 0x800F) -> bytes:
    return encode_atem_packet(
        flags=ATEM_FLAG_RELIABLE,
        session_id=session_id,
        packet_id=packet_id,
        payload=b"".join(commands),
    )


def initial_state_datagram() -> bytes:
    return state_datagram(
        cmd("_ver", b"\x00\x02\x00\x10"),
        cmd("_pin", pin_payload()),
        cmd("InPr", input_payload(1, "Camera 1", "C1")),
        cmd("InPr", input_payload(2, "Camera 2", "C2")),
        cmd("PrgI", b"\x00\x00\x00\x01"),
        cmd("PrvI", b"\x00\x00\x00\x02\x00\x00\x00\x00"),
        cmd("InCm", b""),
    )


def test_handshake_packet_encoding_and_decoding() -> None:
    packet = encode_connect_hello()
    assert packet == bytes.fromhex("10 14 53 ab 00 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00")
    decoded = decode_atem_packet(packet)
    assert decoded.flags == ATEM_FLAG_SYN
    assert decoded.session_id == 0x53AB
    assert decoded.payload == ATEM_CONNECT_PAYLOAD


def test_valid_session_establishment_and_state_parse() -> None:
    fake = FakeUdpSocket([handshake_reply(), initial_state_datagram()])
    client = AtemReadOnlyProbeClient("192.168.1.184", socket_factory=lambda: fake)
    state = client.connect()
    assert client.confirmed is True
    assert state.product_name == "ATEM Television Studio Pro 4K"
    assert state.model_name == "ATEM Television Studio Pro 4K"
    assert state.protocol_version == "2.16"
    assert state.program_source_id == 1
    assert state.preview_source_id == 2
    assert state.inputs[1].long_name == "Camera 1"
    assert all(is_readonly_session_packet(packet) for packet in fake.sent)


def test_invalid_non_atem_udp_response_is_rejected() -> None:
    fake = FakeUdpSocket([b"not-an-atem-response"])
    client = AtemReadOnlyProbeClient("192.168.1.184", socket_factory=lambda: fake)
    with pytest.raises(AtemProtocolError):
        client.connect()
    assert fake.closed is True


def test_model_product_packet_parsing() -> None:
    commands = parse_atem_commands(cmd("_pin", pin_payload("Television Studio 4K", 10)))
    assert commands[0].name == "_pin"
    fake = FakeUdpSocket([handshake_reply(), state_datagram(cmd("_pin", pin_payload("Television Studio 4K", 10)), cmd("InCm", b""))])
    state = AtemReadOnlyProbeClient("192.168.1.184", socket_factory=lambda: fake).connect()
    assert state.product_name == "Television Studio 4K"
    assert state.model_id == 10


def test_program_and_preview_source_parsing() -> None:
    fake = FakeUdpSocket([
        handshake_reply(),
        state_datagram(cmd("PrgI", b"\x00\x00\x03\xe8"), cmd("PrvI", b"\x00\x00\x00\x05\x00\x00\x00\x00"), cmd("InCm", b"")),
    ])
    state = AtemReadOnlyProbeClient("192.168.1.184", socket_factory=lambda: fake).connect()
    assert state.program_source_id == 1000
    assert state.preview_source_id == 5


def test_input_source_name_parsing() -> None:
    fake = FakeUdpSocket([handshake_reply(), state_datagram(cmd("InPr", input_payload(7, "SDI Camera 7", "C7")), cmd("InCm", b""))])
    state = AtemReadOnlyProbeClient("192.168.1.184", socket_factory=lambda: fake).connect()
    assert state.inputs[7].long_name == "SDI Camera 7"
    assert state.inputs[7].short_name == "C7"


def test_multiple_commands_in_one_datagram() -> None:
    payload = cmd("PrgI", b"\x00\x00\x00\x01") + cmd("PrvI", b"\x00\x00\x00\x02\x00\x00\x00\x00")
    parsed = parse_atem_commands(payload)
    assert [item.name for item in parsed] == ["PrgI", "PrvI"]


def test_malformed_command_handling_does_not_crash_receive_loop() -> None:
    fake = FakeUdpSocket([handshake_reply(), initial_state_datagram(), state_datagram(b"\x00\x04bad!", packet_id=3)])
    client = AtemReadOnlyProbeClient("192.168.1.184", socket_factory=lambda: fake)
    client.connect()
    assert client.receive_once() == ()
    assert client.confirmed is True


def test_timeout_behavior() -> None:
    fake = FakeUdpSocket([socket.timeout()])
    client = AtemReadOnlyProbeClient("192.168.1.184", timeout=0.1, socket_factory=lambda: fake)
    with pytest.raises(AtemTimeoutError):
        client.connect()


def test_clean_disconnect() -> None:
    fake = FakeUdpSocket([handshake_reply(), initial_state_datagram()])
    client = AtemReadOnlyProbeClient("192.168.1.184", socket_factory=lambda: fake)
    client.connect()
    client.disconnect()
    assert fake.closed is True
    assert client.connected is False


def test_probe_generates_no_switching_or_state_changing_commands() -> None:
    fake = FakeUdpSocket([handshake_reply(), initial_state_datagram()])
    client = AtemReadOnlyProbeClient("192.168.1.184", socket_factory=lambda: fake)
    client.connect()
    # Only SYN hello and protocol ACKs are permitted. No higher-level payload
    # such as CPgI/CPvI/DCut/DAut may be generated.
    assert len(fake.sent) >= 2
    for packet in fake.sent:
        decoded = decode_atem_packet(packet)
        assert decoded.flags in {ATEM_FLAG_SYN, ATEM_FLAG_ACK}
        if decoded.flags == ATEM_FLAG_ACK:
            assert decoded.payload == b""


def test_manual_update_can_parse_program_preview_changes() -> None:
    fake = FakeUdpSocket([
        handshake_reply(),
        initial_state_datagram(),
        state_datagram(cmd("PrgI", b"\x00\x00\x00\x02"), cmd("PrvI", b"\x00\x00\x00\x01\x00\x00\x00\x00"), packet_id=3),
    ])
    client = AtemReadOnlyProbeClient("192.168.1.184", socket_factory=lambda: fake)
    client.connect()
    commands = client.receive_once()
    assert {c.name for c in commands} == {"PrgI", "PrvI"}
    assert client.state.program_source_id == 2
    assert client.state.preview_source_id == 1

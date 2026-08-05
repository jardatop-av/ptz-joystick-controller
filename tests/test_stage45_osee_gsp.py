from __future__ import annotations

import json
import socket

import pytest

from ptz_joystick_controller.switchers.osee_gsp import (
    GSP_HEADER,
    GspCommand,
    GspCrcError,
    GspStreamParser,
    GspTransportError,
    OseeGspTransport,
    crc16_modbus,
    decode_gsp_packet,
    encode_gsp_command,
)


def test_crc16_modbus_known_vector() -> None:
    assert crc16_modbus(b"123456789") == 0x4B37


def test_encode_get_command() -> None:
    packet = encode_gsp_command(GspCommand(id="device/status", type="get"))
    assert GSP_HEADER == b"\xEB\xA6"
    assert packet.startswith(b"\xEB\xA6\x00")
    length = int.from_bytes(packet[3:5], "little")
    assert length == len(packet) - 5
    assert json.loads(packet[5:-2]) == {"id": "device/status", "type": "get"}
    assert decode_gsp_packet(packet) == GspCommand(id="device/status", type="get")


def test_encode_set_command() -> None:
    command = GspCommand(id="example/set", type="set", value=(1, "CH1", 2.5))
    packet = encode_gsp_command(command)
    assert decode_gsp_packet(packet) == command


@pytest.mark.parametrize("command_type", ["get", "pus"])
def test_decode_get_and_push_packet(command_type: str) -> None:
    command = GspCommand(id="unknown/id/is/allowed", type=command_type, value=(1, "ok"))  # type: ignore[arg-type]
    assert decode_gsp_packet(encode_gsp_command(command)) == command


def test_fragmented_packet_parsing() -> None:
    packet = encode_gsp_command(GspCommand(id="fragmented", type="pus", value=(7,)))
    parser = GspStreamParser()
    assert parser.feed(packet[:3]) == ()
    assert parser.feed(packet[3:9]) == ()
    assert parser.feed(packet[9:]) == (GspCommand(id="fragmented", type="pus", value=(7,)),)


def test_multiple_packets_in_one_buffer() -> None:
    first = GspCommand(id="one", type="get")
    second = GspCommand(id="two", type="pus", value=(2,))
    parser = GspStreamParser()
    assert parser.feed(encode_gsp_command(first) + encode_gsp_command(second)) == (first, second)


def test_invalid_crc_is_rejected_without_crashing() -> None:
    packet = bytearray(encode_gsp_command(GspCommand(id="bad", type="get")))
    packet[-1] ^= 0xFF
    with pytest.raises(GspCrcError):
        decode_gsp_packet(bytes(packet))
    parser = GspStreamParser()
    assert parser.feed(bytes(packet)) == ()
    assert parser.issues[-1].kind == "crc"



def test_invalid_json_is_rejected_without_crashing() -> None:
    payload = b"{not-json"
    length = len(payload) + 2
    prefix = GSP_HEADER + b"\x00" + length.to_bytes(2, "little") + payload
    packet = prefix + crc16_modbus(prefix).to_bytes(2, "little")
    parser = GspStreamParser()
    assert parser.feed(packet) == ()
    assert parser.issues[-1].kind == "protocol"

def test_junk_bytes_before_header_are_skipped() -> None:
    command = GspCommand(id="after-junk", type="pus")
    parser = GspStreamParser()
    assert parser.feed(b"\x00garbage\xff" + encode_gsp_command(command)) == (command,)


class FakeSocket:
    def __init__(self, recv_values: list[bytes | BaseException] | None = None) -> None:
        self.recv_values = list(recv_values or [])
        self.timeout_values: list[float | None] = []
        self.connected_to: tuple[str, int] | None = None
        self.sent: list[bytes] = []
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout_values.append(value)

    def connect(self, address: tuple[str, int]) -> None:
        self.connected_to = address

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        if not self.recv_values:
            raise socket.timeout()
        value = self.recv_values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


def test_transport_disconnect_and_reconnect_creates_new_socket() -> None:
    sockets = [FakeSocket(), FakeSocket()]
    transport = OseeGspTransport("192.0.2.10", socket_factory=lambda: sockets.pop(0))
    transport.connect()
    first_socket = transport._socket
    assert first_socket is not None
    transport.reconnect()
    assert first_socket.closed is True
    assert transport.connected is True
    assert transport._socket is not first_socket


def test_transport_handles_fragmented_receive() -> None:
    command = GspCommand(id="status", type="pus", value=(1,))
    packet = encode_gsp_command(command)
    fake = FakeSocket([packet[:6], packet[6:]])
    transport = OseeGspTransport("192.0.2.10", socket_factory=lambda: fake)
    transport.connect()
    assert transport.receive() == ()
    assert transport.receive() == (command,)


def test_transport_peer_disconnect_clears_connection() -> None:
    fake = FakeSocket([b""])
    transport = OseeGspTransport("192.0.2.10", socket_factory=lambda: fake)
    transport.connect()
    with pytest.raises(GspTransportError):
        transport.receive()
    assert transport.connected is False


def test_send_get_uses_connected_socket() -> None:
    fake = FakeSocket()
    transport = OseeGspTransport("192.0.2.10", socket_factory=lambda: fake)
    transport.connect()
    packet = transport.send_get("foo")
    assert fake.sent == [packet]
    assert decode_gsp_packet(packet) == GspCommand(id="foo", type="get")


def test_real_hardware_wire_header_and_pgm_index_push_packet() -> None:
    # Real-style compact GSP frame using the EB A6 wire header observed on
    # GoStream Duet 8 ISO firmware 2.1.0.
    packet = bytes.fromhex(
        "eb a6 00 2c 00 "
        "7b 22 69 64 22 3a 22 70 67 6d 49 6e 64 65 78 22 2c "
        "22 74 79 70 65 22 3a 22 70 75 73 22 2c 22 76 61 6c "
        "75 65 22 3a 5b 31 5d 7d 8b f8"
    )
    assert packet[:2] == b"\xEB\xA6"
    assert decode_gsp_packet(packet) == GspCommand(id="pgmIndex", type="pus", value=(1,))


def test_probe_human_readable_command_format() -> None:
    from ptz_joystick_controller.switchers.osee_gsp import format_gsp_command

    assert format_gsp_command(GspCommand(id="pgmIndex", type="pus", value=(1,))) == "pus pgmIndex = [1]"
    assert format_gsp_command(GspCommand(id="pvwIndex", type="pus", value=(5001,))) == "pus pvwIndex = [5001]"
    assert format_gsp_command(GspCommand(id="transitionStatus", type="pus", value=(1,))) == "pus transitionStatus = [1]"

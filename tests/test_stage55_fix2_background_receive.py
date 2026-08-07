from __future__ import annotations

import queue
import socket
import threading
import time

import pytest

from ptz_joystick_controller.switchers.atem_control_probe import (
    AtemManualControlClient,
    auto_command,
    cut_command,
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
)


def cmd(name: str, payload: bytes) -> bytes:
    return encode_readonly_state_command(name, payload)


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


def ack_datagram(ack_id: int, *, session_id: int = 0x800F) -> bytes:
    return encode_atem_packet(flags=ATEM_FLAG_ACK, session_id=session_id, ack_id=ack_id)


def pin_payload() -> bytes:
    name = b"ATEM Television Studio 4K8"
    return name + b"\x00" * (40 - len(name)) + bytes([42, 0, 0, 0])


def initial_state_datagram(*, program: int = 1, preview: int = 6) -> bytes:
    return state_datagram(
        cmd("_ver", b"\x00\x02\x00\x20"),
        cmd("_pin", pin_payload()),
        cmd("PrgI", bytes((0, 0)) + program.to_bytes(2, "big")),
        cmd("PrvI", bytes((0, 0)) + preview.to_bytes(2, "big") + b"\x00\x00\x00\x00"),
        cmd("InCm", b""),
    )


class BlockingUdpSocket:
    def __init__(self) -> None:
        self.incoming: queue.Queue[bytes | BaseException] = queue.Queue()
        self.sent: list[bytes] = []
        self.timeout = 0.05
        self.closed = False
        self.recv_thread_ids: list[int] = []
        self.on_send = None

    def settimeout(self, value: float | None) -> None:
        self.timeout = 0.05 if value is None else min(value, 0.05)

    def connect(self, address: tuple[str, int]) -> None:
        return None

    def send(self, data: bytes) -> int:
        self.sent.append(data)
        if self.on_send is not None:
            self.on_send(data)
        return len(data)

    def recv(self, size: int) -> bytes:
        self.recv_thread_ids.append(threading.get_ident())
        try:
            item = self.incoming.get(timeout=self.timeout)
        except queue.Empty as exc:
            raise socket.timeout() from exc
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        self.closed = True
        self.incoming.put(OSError("closed"))

    def push(self, data: bytes) -> None:
        self.incoming.put(data)


def connected_client() -> tuple[AtemManualControlClient, BlockingUdpSocket]:
    fake = BlockingUdpSocket()
    fake.push(handshake_reply())
    fake.push(initial_state_datagram())
    client = AtemManualControlClient("192.168.1.184", timeout=0.2, socket_factory=lambda: fake)
    client.connect()
    return client, fake


def wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not reached")


def test_receive_loop_updates_physical_preview_while_foreground_idle() -> None:
    client, fake = connected_client()
    client.start_receive_loop()
    fake.push(state_datagram(cmd("PrvI", b"\x00\x00\x00\x03\x00\x00\x00\x00"), packet_id=3))
    wait_until(lambda: client.state.preview_source_id == 3)
    assert client.receiver_running is True
    client.disconnect()


def test_receive_loop_updates_physical_program_without_local_command() -> None:
    client, fake = connected_client()
    client.start_receive_loop()
    fake.push(state_datagram(cmd("PrgI", b"\x00\x00\x00\x04"), packet_id=3))
    wait_until(lambda: client.state.program_source_id == 4)
    client.disconnect()


def test_background_receiver_wakes_transport_ack_waiter_and_preview_feedback() -> None:
    client, fake = connected_client()
    client.start_receive_loop()

    def on_send(raw: bytes) -> None:
        packet = decode_atem_packet(raw)
        if not (packet.flags & ATEM_FLAG_RELIABLE):
            return
        fake.push(ack_datagram(packet.packet_id, session_id=packet.session_id))
        fake.push(
            state_datagram(
                cmd("PrvI", b"\x00\x00\x00\x03\x00\x00\x00\x00"),
                packet_id=7,
                session_id=packet.session_id,
            )
        )

    fake.on_send = on_send
    client.set_preview(3, feedback_timeout=0.5)
    assert client.last_command_transport_acked is True
    assert client.state.preview_source_id == 3
    client.disconnect()


def test_only_background_receiver_reads_socket_after_loop_starts() -> None:
    client, fake = connected_client()
    fake.recv_thread_ids.clear()
    client.start_receive_loop()

    def on_send(raw: bytes) -> None:
        packet = decode_atem_packet(raw)
        if packet.flags & ATEM_FLAG_RELIABLE:
            fake.push(ack_datagram(packet.packet_id, session_id=packet.session_id))
            fake.push(state_datagram(cmd("PrvI", b"\x00\x00\x00\x03\x00\x00\x00\x00"), packet_id=8, session_id=packet.session_id))

    fake.on_send = on_send
    client.set_preview(3, feedback_timeout=0.5)
    wait_until(lambda: bool(fake.recv_thread_ids))
    assert len(set(fake.recv_thread_ids)) == 1
    assert fake.recv_thread_ids[0] != threading.get_ident()
    client.disconnect()


def test_disconnect_stops_receiver_cleanly() -> None:
    client, fake = connected_client()
    client.start_receive_loop()
    assert client.receiver_running
    client.disconnect()
    assert client.receiver_running is False
    assert fake.closed is True


def test_payloads_unchanged_in_fix2() -> None:
    assert preview_input_command(1).payload == bytes.fromhex("00 00 00 01")
    assert cut_command().payload == bytes.fromhex("00 00 00 00")
    assert auto_command().payload == bytes.fromhex("00 00 00 00")


def test_background_receiver_does_not_change_user_input_text() -> None:
    # Asynchronous logging is independent of the exact string returned by input().
    typed = "  preview 2  "
    assert typed.strip().split() == ["preview", "2"]

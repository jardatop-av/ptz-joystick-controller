from __future__ import annotations

import logging

from ptz_joystick_controller.switchers.atem_control_probe import AtemManualControlClient
from ptz_joystick_controller.switchers.atem_probe import (
    ATEM_FLAG_ACK,
    ATEM_FLAG_RELIABLE,
    ATEM_INITIAL_SESSION_ID,
    decode_atem_packet,
    encode_atem_packet,
    encode_readonly_state_command,
)

from test_stage55_fix2_background_receive import (
    BlockingUdpSocket,
    ack_datagram,
    connected_client,
    state_datagram,
    wait_until,
)


def cmd(name: str, payload: bytes) -> bytes:
    return encode_readonly_state_command(name, payload)


def test_time_packet_is_received_and_acked_without_raw_debug_spam(caplog) -> None:
    client, fake = connected_client()
    client._debug = True
    client._trace_packets = False
    caplog.set_level(logging.DEBUG)
    client.start_receive_loop()

    fake.push(state_datagram(cmd("Time", b"\x00\x00\x00\x00\x00\x00\x00\x00"), packet_id=44))
    wait_until(
        lambda: any(
            (packet := decode_atem_packet(raw)).flags & ATEM_FLAG_ACK and packet.ack_id == 44
            for raw in fake.sent
        )
    )

    text = caplog.text
    assert "ATEM RECV" not in text
    assert "ATEM SEND ACK" not in text
    client.disconnect()


def test_routine_reliable_state_ack_does_not_spam_debug(caplog) -> None:
    client, fake = connected_client()
    client._debug = True
    client._trace_packets = False
    caplog.set_level(logging.DEBUG)
    client.start_receive_loop()
    fake.push(state_datagram(cmd("PrvI", b"\x00\x00\x00\x03\x00\x00\x00\x00"), packet_id=45))
    wait_until(lambda: client.state.preview_source_id == 3)
    assert "ATEM SEND ACK" not in caplog.text
    assert "ATEM STATE Preview changed: 3 ->" in caplog.text
    client.disconnect()


def test_command_ack_diagnostic_remains_visible(caplog) -> None:
    client, fake = connected_client()
    client._debug = True
    client._trace_packets = False
    caplog.set_level(logging.DEBUG)
    client.start_receive_loop()

    def on_send(raw: bytes) -> None:
        packet = decode_atem_packet(raw)
        if not (packet.flags & ATEM_FLAG_RELIABLE):
            return
        fake.push(ack_datagram(packet.packet_id, session_id=packet.session_id))
        fake.push(
            state_datagram(
                cmd("PrvI", b"\x00\x00\x00\x03\x00\x00\x00\x00"),
                packet_id=46,
                session_id=packet.session_id,
            )
        )

    fake.on_send = on_send
    client.set_preview(3, feedback_timeout=0.5)
    assert "ATEM COMMAND SEND packet_id=1 command=CPvI" in caplog.text
    assert "ATEM COMMAND ACK packet_id=1" in caplog.text
    assert "ATEM RECV" not in caplog.text
    client.disconnect()


def test_trace_packets_explicitly_restores_raw_hex_logging(caplog) -> None:
    client, fake = connected_client()
    client._debug = True
    client._trace_packets = True
    caplog.set_level(logging.DEBUG)
    client.start_receive_loop()
    fake.push(state_datagram(cmd("Time", b"\x00" * 8), packet_id=47))
    wait_until(lambda: any(decode_atem_packet(raw).ack_id == 47 for raw in fake.sent if len(raw) >= 12))
    assert "ATEM RECV" in caplog.text
    assert "ATEM SEND ACK" in caplog.text
    client.disconnect()


def test_interactive_input_text_parsing_remains_independent_of_logging() -> None:
    typed = "  preview 2  "
    assert typed.strip().split() == ["preview", "2"]

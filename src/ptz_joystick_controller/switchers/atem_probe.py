from __future__ import annotations

from dataclasses import dataclass, field
import logging
import socket
import time
from typing import Callable, Protocol

ATEM_DEFAULT_PORT = 9910
ATEM_HEADER_SIZE = 12
ATEM_INITIAL_SESSION_ID = 0x53AB
ATEM_MAX_PACKET_LENGTH = 0x07FF

# Wire flag values occupy the high five bits of the first 16-bit word.
ATEM_FLAG_RELIABLE = 0x01
ATEM_FLAG_SYN = 0x02
ATEM_FLAG_RETRANSMIT = 0x04
ATEM_FLAG_RETRANSMIT_REQUEST = 0x08
ATEM_FLAG_ACK = 0x10

ATEM_CONNECT_PAYLOAD = b"\x01\x00\x00\x00\x00\x00\x00\x00"


class AtemProbeError(Exception):
    """Base error for the isolated read-only ATEM probe."""


class AtemProtocolError(AtemProbeError):
    """Malformed or unexpected ATEM protocol data."""


class AtemTimeoutError(TimeoutError, AtemProbeError):
    """ATEM did not complete the read-only session in time."""


@dataclass(frozen=True, slots=True)
class AtemPacket:
    flags: int
    length: int
    session_id: int
    ack_id: int
    remote_sequence: int
    packet_id: int
    payload: bytes = b""


@dataclass(frozen=True, slots=True)
class AtemCommand:
    name: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class AtemInputInfo:
    source_id: int
    long_name: str
    short_name: str


@dataclass(slots=True)
class AtemReadOnlyState:
    product_name: str | None = None
    model_id: int | None = None
    model_name: str | None = None
    protocol_version: str | None = None
    program_source_id: int | None = None
    preview_source_id: int | None = None
    inputs: dict[int, AtemInputInfo] = field(default_factory=dict)
    init_complete: bool = False
    transition_in_progress: bool = False
    transition_frames_remaining: int | None = None
    transition_position: int | None = None

    def source_label(self, source_id: int | None) -> str:
        if source_id is None:
            return "unknown"
        info = self.inputs.get(source_id)
        return info.long_name if info and info.long_name else f"Source {source_id}"


_MODEL_NAMES = {
    0: "Unknown",
    1: "ATEM Television Studio",
    2: "ATEM 1 M/E",
    3: "ATEM 2 M/E",
    4: "ATEM Production Studio 4K",
    5: "ATEM 1 M/E 4K",
    6: "ATEM 2 M/E 4K",
    7: "ATEM 2 M/E Broadcast Studio 4K",
    8: "ATEM Television Studio HD",
    9: "ATEM Television Studio Pro HD",
    10: "ATEM Television Studio Pro 4K",
    11: "ATEM Constellation",
    12: "ATEM Constellation 8K",
    13: "ATEM Mini",
    14: "ATEM Mini Pro",
    15: "ATEM Mini Pro ISO",
}


def _decode_c_string(value: bytes) -> str:
    return value.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


def encode_atem_packet(
    *,
    flags: int,
    session_id: int,
    payload: bytes = b"",
    ack_id: int = 0,
    remote_sequence: int = 0,
    packet_id: int = 0,
) -> bytes:
    length = ATEM_HEADER_SIZE + len(payload)
    if length > ATEM_MAX_PACKET_LENGTH:
        raise ValueError("ATEM packet too large")
    if not 0 <= flags <= 0x1F:
        raise ValueError("ATEM flags out of range")
    first_word = (flags << 11) | length
    return b"".join(
        (
            first_word.to_bytes(2, "big"),
            session_id.to_bytes(2, "big"),
            ack_id.to_bytes(2, "big"),
            b"\x00\x00",
            remote_sequence.to_bytes(2, "big"),
            packet_id.to_bytes(2, "big"),
            payload,
        )
    )


def encode_connect_hello(session_id: int = ATEM_INITIAL_SESSION_ID) -> bytes:
    return encode_atem_packet(flags=ATEM_FLAG_SYN, session_id=session_id, payload=ATEM_CONNECT_PAYLOAD)


def encode_ack(session_id: int, packet_id: int, *, remote_sequence: int = 0) -> bytes:
    return encode_atem_packet(
        flags=ATEM_FLAG_ACK,
        session_id=session_id,
        ack_id=packet_id,
        remote_sequence=remote_sequence,
    )


def decode_atem_packet(data: bytes) -> AtemPacket:
    if len(data) < ATEM_HEADER_SIZE:
        raise AtemProtocolError("ATEM datagram shorter than 12-byte header")
    first_word = int.from_bytes(data[0:2], "big")
    flags = (first_word >> 11) & 0x1F
    length = first_word & 0x07FF
    if length < ATEM_HEADER_SIZE or length > len(data):
        raise AtemProtocolError(f"Invalid ATEM packet length {length} for datagram size {len(data)}")
    return AtemPacket(
        flags=flags,
        length=length,
        session_id=int.from_bytes(data[2:4], "big"),
        ack_id=int.from_bytes(data[4:6], "big"),
        remote_sequence=int.from_bytes(data[8:10], "big"),
        packet_id=int.from_bytes(data[10:12], "big"),
        payload=data[12:length],
    )


def parse_atem_commands(payload: bytes) -> tuple[AtemCommand, ...]:
    commands: list[AtemCommand] = []
    offset = 0
    while offset < len(payload):
        if len(payload) - offset < 8:
            # Some handshake/session packets have non-command payload. Once state
            # streaming starts, truncated command data is malformed.
            raise AtemProtocolError("Truncated ATEM command header")
        command_length = int.from_bytes(payload[offset : offset + 2], "big")
        if command_length < 8 or offset + command_length > len(payload):
            raise AtemProtocolError(f"Invalid ATEM command length {command_length}")
        try:
            name = payload[offset + 4 : offset + 8].decode("ascii")
        except UnicodeDecodeError as exc:
            raise AtemProtocolError("ATEM command name is not ASCII") from exc
        commands.append(AtemCommand(name=name, payload=payload[offset + 8 : offset + command_length]))
        offset += command_length
    return tuple(commands)


def encode_readonly_state_command(name: str, payload: bytes) -> bytes:
    """Test/helper encoder for incoming state command framing only.

    The read-only probe never sends these commands to a switcher.
    """
    if len(name) != 4 or not name.isascii():
        raise ValueError("ATEM command name must contain four ASCII characters")
    length = 8 + len(payload)
    return length.to_bytes(2, "big") + b"\x00\x00" + name.encode("ascii") + payload


def apply_state_command(state: AtemReadOnlyState, command: AtemCommand) -> bool:
    """Apply a known to-client state command. Return True when state changed."""
    payload = command.payload
    if command.name == "_ver":
        if len(payload) < 4:
            raise AtemProtocolError("_ver payload too short")
        major = int.from_bytes(payload[0:2], "big")
        minor = int.from_bytes(payload[2:4], "big")
        state.protocol_version = f"{major}.{minor}"
        return True
    if command.name == "_pin":
        if len(payload) < 41:
            raise AtemProtocolError("_pin payload too short")
        state.product_name = _decode_c_string(payload[0:40])
        state.model_id = payload[40]
        state.model_name = _MODEL_NAMES.get(state.model_id, f"Model {state.model_id}")
        return True
    if command.name in {"PrgI", "PrvI"}:
        if len(payload) < 4:
            raise AtemProtocolError(f"{command.name} payload too short")
        me_index = payload[0]
        source_id = int.from_bytes(payload[2:4], "big")
        if me_index != 0:
            return False
        if command.name == "PrgI":
            state.program_source_id = source_id
        else:
            state.preview_source_id = source_id
        return True
    if command.name == "InPr":
        if len(payload) < 26:
            raise AtemProtocolError("InPr payload too short")
        source_id = int.from_bytes(payload[0:2], "big")
        state.inputs[source_id] = AtemInputInfo(
            source_id=source_id,
            long_name=_decode_c_string(payload[2:22]),
            short_name=_decode_c_string(payload[22:26]),
        )
        return True
    if command.name == "TrPs":
        if len(payload) < 6:
            raise AtemProtocolError("TrPs payload too short")
        me_index = payload[0]
        if me_index != 0:
            return False
        state.transition_in_progress = bool(payload[1])
        state.transition_frames_remaining = payload[2]
        state.transition_position = int.from_bytes(payload[4:6], "big")
        return True
    if command.name == "InCm":
        state.init_complete = True
        return True
    return False


class DatagramSocketLike(Protocol):
    def settimeout(self, value: float | None) -> None: ...
    def connect(self, address: tuple[str, int]) -> None: ...
    def send(self, data: bytes) -> int: ...
    def recv(self, size: int) -> bytes: ...
    def close(self) -> None: ...


SocketFactory = Callable[[], DatagramSocketLike]


def _socket_factory() -> DatagramSocketLike:
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


class AtemReadOnlyProbeClient:
    """Minimal read-only ATEM UDP session.

    Only the mandatory SYN/ACK handshake and ACK maintenance packets are ever
    transmitted. No higher-level ATEM command is sent by this class.
    """

    def __init__(
        self,
        host: str,
        port: int = ATEM_DEFAULT_PORT,
        *,
        timeout: float = 2.0,
        socket_factory: SocketFactory | None = None,
        logger: logging.Logger | None = None,
        debug: bool = False,
        trace_packets: bool | None = None,
    ) -> None:
        if not host.strip():
            raise ValueError("ATEM host must not be empty")
        if not 1 <= port <= 65535:
            raise ValueError("ATEM port must be in range 1..65535")
        if timeout <= 0:
            raise ValueError("ATEM timeout must be positive")
        self.host = host.strip()
        self.port = port
        self.timeout = timeout
        self._socket_factory = socket_factory or _socket_factory
        self._logger = logger or logging.getLogger(__name__)
        self._debug = debug
        # Packet hex tracing is intentionally separate from human-readable
        # DEBUG diagnostics.  ``None`` preserves the historical Stage54
        # behaviour for callers that have not opted into the new control-tool
        # flag; the Stage55 interactive script passes an explicit bool.
        self._trace_packets = debug if trace_packets is None else trace_packets
        self._socket: DatagramSocketLike | None = None
        self.session_id = ATEM_INITIAL_SESSION_ID
        self.state = AtemReadOnlyState()
        self.confirmed = False
        # Client-local reliable sequence state belongs to the UDP session itself.
        # SYN and ACK packets do not consume a local packet id.
        self._local_packet_id = 0
        self._acked_local_packet_ids: set[int] = set()
        self._last_remote_packet_id = 0

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self) -> AtemReadOnlyState:
        self.disconnect()
        self.session_id = ATEM_INITIAL_SESSION_ID
        self.state = AtemReadOnlyState()
        self._local_packet_id = 0
        self._acked_local_packet_ids.clear()
        self._last_remote_packet_id = 0
        sock = self._socket_factory()
        sock.settimeout(self.timeout)
        sock.connect((self.host, self.port))
        self._socket = sock
        try:
            self._send(encode_connect_hello(self.session_id), "SYN")
            response = self._recv_packet()
            if not (response.flags & ATEM_FLAG_SYN):
                raise AtemProtocolError("ATEM handshake response did not contain SYN")
            if response.session_id != self.session_id:
                raise AtemProtocolError("ATEM handshake session id mismatch")
            if not response.payload or response.payload[0] != 0x02:
                status = response.payload[0] if response.payload else None
                raise AtemProtocolError(f"ATEM handshake rejected or invalid status: {status!r}")
            self._send(encode_ack(self.session_id, response.packet_id), "ACK")

            deadline = time.monotonic() + self.timeout
            received_state = False
            while time.monotonic() < deadline:
                try:
                    packet = self._recv_packet()
                except AtemTimeoutError:
                    break
                commands = self._process_session_packet(packet, context="initialization")
                for command in commands:
                    try:
                        changed = apply_state_command(self.state, command)
                    except AtemProtocolError as exc:
                        self._logger.debug("Ignoring malformed ATEM command %s: %s", command.name, exc)
                        continue
                    received_state = received_state or changed
                if self.state.init_complete:
                    break
            if not received_state:
                raise AtemProtocolError("No valid ATEM state packets received after handshake")
            self.confirmed = True
            return self.state
        except Exception:
            self.disconnect()
            raise

    def receive_once(self) -> tuple[AtemCommand, ...]:
        if self._socket is None:
            raise AtemProbeError("ATEM probe is not connected")
        packet = self._recv_packet()
        commands = self._process_session_packet(packet, context="state")
        accepted: list[AtemCommand] = []
        for command in commands:
            try:
                apply_state_command(self.state, command)
            except AtemProtocolError as exc:
                self._logger.debug("Ignoring malformed ATEM command %s: %s", command.name, exc)
                continue
            accepted.append(command)
        return tuple(accepted)

    def _process_session_packet(
        self,
        packet: AtemPacket,
        *,
        context: str,
    ) -> tuple[AtemCommand, ...]:
        """Maintain reliable-session state and return any higher-level commands.

        ACK-only packets are consumed here and are never handed to the state
        command parser. Reliable switcher packets continue to receive the same
        mandatory ACK handling verified by Stage54.
        """
        if packet.session_id:
            self.session_id = packet.session_id
        if packet.flags & ATEM_FLAG_ACK and packet.ack_id:
            self._acked_local_packet_ids.add(packet.ack_id)
            self._logger.debug("ATEM transport ACK received packet_id=%d", packet.ack_id)
        if packet.flags & ATEM_FLAG_RELIABLE:
            self._last_remote_packet_id = packet.packet_id
            self._send(encode_ack(self.session_id, packet.packet_id), "ACK")
        if not packet.payload:
            return ()
        try:
            return parse_atem_commands(packet.payload)
        except AtemProtocolError as exc:
            self._logger.debug("Ignoring malformed ATEM %s packet: %s", context, exc)
            return ()

    @property
    def next_local_packet_id_value(self) -> int:
        """Return the next reliable client packet id without consuming it."""
        return 1 if self._local_packet_id >= 0xFFFF else self._local_packet_id + 1

    def next_local_packet_id(self) -> int:
        """Allocate the next reliable client packet id for this active session."""
        self._local_packet_id = self.next_local_packet_id_value
        return self._local_packet_id

    def is_local_packet_acked(self, packet_id: int) -> bool:
        return packet_id in self._acked_local_packet_ids

    def consume_local_packet_ack(self, packet_id: int) -> bool:
        if packet_id not in self._acked_local_packet_ids:
            return False
        self._acked_local_packet_ids.remove(packet_id)
        return True

    def disconnect(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None
        self.confirmed = False

    def _recv_packet(self) -> AtemPacket:
        if self._socket is None:
            raise AtemProbeError("ATEM probe is not connected")
        try:
            data = self._socket.recv(65535)
        except socket.timeout as exc:
            raise AtemTimeoutError("Timed out waiting for ATEM UDP response") from exc
        except OSError as exc:
            raise AtemProbeError(f"ATEM UDP receive failed: {exc}") from exc
        if self._trace_packets:
            self._logger.debug("ATEM RECV %s", data.hex(" "))
        return decode_atem_packet(data)

    def _send(self, data: bytes, purpose: str) -> None:
        if self._socket is None:
            raise AtemProbeError("ATEM probe is not connected")
        if self._trace_packets:
            self._logger.debug("ATEM SEND %s %s", purpose, data.hex(" "))
        try:
            self._socket.send(data)
        except OSError as exc:
            raise AtemProbeError(f"ATEM UDP send failed: {exc}") from exc


def is_readonly_session_packet(packet: bytes) -> bool:
    """Return True only for handshake/ACK packets generated by this probe."""
    decoded = decode_atem_packet(packet)
    return decoded.flags in {ATEM_FLAG_SYN, ATEM_FLAG_ACK} and (
        decoded.flags != ATEM_FLAG_SYN or decoded.payload == ATEM_CONNECT_PAYLOAD
    )

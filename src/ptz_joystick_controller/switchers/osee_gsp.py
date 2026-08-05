from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import socket
from typing import Callable, Iterable, Literal, Protocol

GSP_HEADER = b"\xEB\xA6"
GSP_PROTO_ID = 0
GSP_MIN_LENGTH = 2  # CRC only
GSP_MAX_LENGTH = 0xFFFF
GspCommandType = Literal["get", "set", "pus", "res"]


class GspError(Exception):
    """Base error for GoStream Series Protocol operations."""


class GspProtocolError(GspError):
    """Raised for malformed GSP packets or commands."""


class GspCrcError(GspProtocolError):
    """Raised when a GSP packet CRC does not match."""


class GspTransportError(ConnectionError, GspError):
    """Raised when the TCP transport cannot connect, send, or receive."""


@dataclass(frozen=True, slots=True)
class GspCommand:
    id: str
    type: GspCommandType
    value: tuple[int | float | str, ...] | None = None

    def to_json_object(self) -> dict[str, object]:
        result: dict[str, object] = {"id": self.id, "type": self.type}
        if self.value is not None:
            result["value"] = list(self.value)
        return result

    @classmethod
    def from_json_object(cls, value: object) -> "GspCommand":
        if not isinstance(value, dict):
            raise GspProtocolError("GSP JSON payload must be an object")
        command_id = value.get("id")
        command_type = value.get("type")
        command_value = value.get("value")
        if not isinstance(command_id, str) or not command_id:
            raise GspProtocolError("GSP command id must be a non-empty string")
        if command_type not in ("get", "set", "pus", "res"):
            raise GspProtocolError(f"Unsupported GSP command type: {command_type!r}")
        parsed_value: tuple[int | float | str, ...] | None = None
        if command_value is not None:
            if not isinstance(command_value, list):
                raise GspProtocolError("GSP command value must be an array")
            if not all(isinstance(item, (int, float, str)) and not isinstance(item, bool) for item in command_value):
                raise GspProtocolError("GSP command value items must be numbers or strings")
            parsed_value = tuple(command_value)
        return cls(id=command_id, type=command_type, value=parsed_value)


def format_gsp_command(command: GspCommand) -> str:
    """Return a compact, human-readable decoded command for probes/logging."""
    if command.value is None:
        return f"{command.type} {command.id}"
    return f"{command.type} {command.id} = {list(command.value)}"


def crc16_modbus(data: bytes, initial: int = 0xFFFF) -> int:
    """Return standard CRC16/Modbus for *data*."""
    crc = initial & 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def encode_gsp_command(command: GspCommand, *, proto_id: int = GSP_PROTO_ID) -> bytes:
    if not 0 <= proto_id <= 0xFF:
        raise ValueError("proto_id must be in range 0..255")
    payload = json.dumps(command.to_json_object(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    length = len(payload) + 2
    if length > GSP_MAX_LENGTH:
        raise ValueError("GSP payload is too large")
    packet_without_crc = GSP_HEADER + bytes((proto_id,)) + length.to_bytes(2, "little") + payload
    crc = crc16_modbus(packet_without_crc)
    return packet_without_crc + crc.to_bytes(2, "little")


def decode_gsp_packet(packet: bytes) -> GspCommand:
    if len(packet) < 7:
        raise GspProtocolError("GSP packet is too short")
    if packet[:2] != GSP_HEADER:
        raise GspProtocolError("Invalid GSP header")
    length = int.from_bytes(packet[3:5], "little")
    if length < GSP_MIN_LENGTH:
        raise GspProtocolError("Invalid GSP length")
    expected_size = 5 + length
    if len(packet) != expected_size:
        raise GspProtocolError(f"GSP packet length mismatch: expected {expected_size}, got {len(packet)}")
    expected_crc = int.from_bytes(packet[-2:], "little")
    actual_crc = crc16_modbus(packet[:-2])
    if actual_crc != expected_crc:
        raise GspCrcError(f"GSP CRC mismatch: expected 0x{expected_crc:04X}, calculated 0x{actual_crc:04X}")
    try:
        decoded = json.loads(packet[5:-2].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GspProtocolError(f"Invalid GSP JSON payload: {exc}") from exc
    return GspCommand.from_json_object(decoded)


@dataclass(slots=True)
class GspParseIssue:
    kind: str
    message: str
    packet_hex: str | None = None


class GspStreamParser:
    """Buffered parser for the TCP byte stream used by GSP."""

    def __init__(self, *, logger: logging.Logger | None = None, max_length: int = GSP_MAX_LENGTH) -> None:
        self._buffer = bytearray()
        self._logger = logger or logging.getLogger(__name__)
        self._max_length = max_length
        self.issues: list[GspParseIssue] = []

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()
        self.issues.clear()

    def feed(self, data: bytes) -> tuple[GspCommand, ...]:
        if data:
            self._buffer.extend(data)
        commands: list[GspCommand] = []
        while True:
            header_index = self._buffer.find(GSP_HEADER)
            if header_index < 0:
                # Keep a possible first header byte for the next fragmented recv.
                if self._buffer.endswith(GSP_HEADER[:1]):
                    del self._buffer[:-1]
                else:
                    self._buffer.clear()
                break
            if header_index:
                del self._buffer[:header_index]
            if len(self._buffer) < 5:
                break
            length = int.from_bytes(self._buffer[3:5], "little")
            if length < GSP_MIN_LENGTH or length > self._max_length:
                self._record_issue("length", f"Invalid GSP length: {length}")
                del self._buffer[0]
                continue
            packet_size = 5 + length
            if len(self._buffer) < packet_size:
                break
            packet = bytes(self._buffer[:packet_size])
            del self._buffer[:packet_size]
            try:
                commands.append(decode_gsp_packet(packet))
            except GspCrcError as exc:
                self._record_issue("crc", str(exc), packet)
            except GspProtocolError as exc:
                self._record_issue("protocol", str(exc), packet)
        return tuple(commands)

    def _record_issue(self, kind: str, message: str, packet: bytes | None = None) -> None:
        packet_hex = packet.hex(" ") if packet is not None else None
        self.issues.append(GspParseIssue(kind=kind, message=message, packet_hex=packet_hex))
        self._logger.warning("Osee GSP parser ignored %s error: %s", kind, message)


class SocketLike(Protocol):
    def settimeout(self, value: float | None) -> None: ...
    def connect(self, address: tuple[str, int]) -> None: ...
    def sendall(self, data: bytes) -> None: ...
    def recv(self, size: int) -> bytes: ...
    def close(self) -> None: ...


SocketFactory = Callable[[], SocketLike]


def _default_socket_factory() -> SocketLike:
    return socket.socket(socket.AF_INET, socket.SOCK_STREAM)


class OseeGspTransport:
    """Reconnect-safe TCP transport for Osee GoStream Series Protocol.

    This layer only frames/parses GSP commands. Model-specific command IDs and
    source mappings intentionally live outside this module.
    """

    def __init__(
        self,
        host: str,
        port: int = 19010,
        *,
        connect_timeout: float = 2.0,
        read_timeout: float = 0.5,
        recv_size: int = 4096,
        socket_factory: SocketFactory | None = None,
        logger: logging.Logger | None = None,
        debug: bool = False,
    ) -> None:
        if not host.strip():
            raise ValueError("Osee GSP host must not be empty")
        if not 1 <= port <= 65535:
            raise ValueError("Osee GSP port must be in range 1..65535")
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("Timeouts must be positive")
        self.host = host.strip()
        self.port = port
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.recv_size = recv_size
        self._socket_factory = socket_factory or _default_socket_factory
        self._logger = logger or logging.getLogger(__name__)
        self._debug = debug
        self._socket: SocketLike | None = None
        self._parser = GspStreamParser(logger=self._logger)

    @property
    def connected(self) -> bool:
        return self._socket is not None

    @property
    def parser(self) -> GspStreamParser:
        return self._parser

    def connect(self) -> None:
        if self.connected:
            return
        sock = self._socket_factory()
        try:
            sock.settimeout(self.connect_timeout)
            sock.connect((self.host, self.port))
            sock.settimeout(self.read_timeout)
        except (OSError, TimeoutError) as exc:
            try:
                sock.close()
            finally:
                self._socket = None
            raise GspTransportError(f"Unable to connect to Osee GSP {self.host}:{self.port}: {exc}") from exc
        self._socket = sock
        self._parser.reset()
        self._logger.info("Connected to Osee GSP %s:%s", self.host, self.port)

    def disconnect(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError as exc:
                self._logger.debug("Osee GSP socket close failed: %s", exc)
        self._parser.reset()

    def reconnect(self) -> None:
        self.disconnect()
        self.connect()

    def send_command(self, command: GspCommand) -> bytes:
        sock = self._require_socket()
        packet = encode_gsp_command(command)
        if self._debug:
            self._logger.debug("Osee GSP send %s:%s %s", self.host, self.port, packet.hex(" "))
        try:
            sock.sendall(packet)
        except (OSError, TimeoutError) as exc:
            self.disconnect()
            raise GspTransportError(f"Osee GSP send failed: {exc}") from exc
        return packet

    def send_get(self, command_id: str) -> bytes:
        return self.send_command(GspCommand(id=command_id, type="get"))

    def send_set(self, command_id: str, values: Iterable[int | float | str]) -> bytes:
        return self.send_command(GspCommand(id=command_id, type="set", value=tuple(values)))

    def receive(self) -> tuple[GspCommand, ...]:
        sock = self._require_socket()
        try:
            data = sock.recv(self.recv_size)
        except socket.timeout:
            return ()
        except TimeoutError:
            return ()
        except OSError as exc:
            self.disconnect()
            raise GspTransportError(f"Osee GSP receive failed: {exc}") from exc
        if data == b"":
            self.disconnect()
            raise GspTransportError("Osee GSP peer disconnected")
        if self._debug:
            self._logger.debug("Osee GSP recv %s:%s %s", self.host, self.port, data.hex(" "))
        return self._parser.feed(data)

    def _require_socket(self) -> SocketLike:
        if self._socket is None:
            raise GspTransportError("Osee GSP transport is not connected")
        return self._socket

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass, field
from typing import Protocol

from ..models.ptz import PtzCamera

LOGGER = logging.getLogger(__name__)


class PtzTransport(Protocol):
    connected: bool

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def send(self, packet: bytes) -> None: ...


@dataclass
class FakeViscaTransport:
    connected: bool = False
    sent_packets: list[bytes] = field(default_factory=list)
    fail_sends: bool = False
    connect_count: int = 0
    disconnect_count: int = 0

    def connect(self) -> None:
        self.connected = True
        self.connect_count += 1

    def disconnect(self) -> None:
        self.connected = False
        self.disconnect_count += 1

    def send(self, packet: bytes) -> None:
        if not self.connected:
            self.connect()
        if self.fail_sends:
            self.connected = False
            raise ConnectionError("Fake VISCA transport send failed")
        self.sent_packets.append(packet)


@dataclass
class UdpViscaTransport:
    """Real VISCA-over-IP UDP transport.

    The transport is intentionally small and reconnect-safe: it opens a UDP
    socket lazily, logs packets before sending, and recreates the socket after
    send errors. VISCA ACK/response parsing is intentionally not implemented in
    Stage16; timeout handling is provided through the socket timeout.
    """

    host: str
    port: int = 52381
    timeout_seconds: float = 0.5
    connected: bool = False
    sent_packets: list[bytes] = field(default_factory=list)
    _socket: socket.socket | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.host = (self.host or "").strip()
        if not self.host:
            raise ValueError("VISCA camera host must be configured")
        if not 1 <= self.port <= 65535:
            raise ValueError("VISCA camera port must be in range 1..65535")
        if self.timeout_seconds <= 0:
            raise ValueError("VISCA timeout must be positive")

    @classmethod
    def from_camera(cls, camera: PtzCamera, timeout_seconds: float = 0.5) -> "UdpViscaTransport":
        if not camera.enabled:
            raise ValueError(f"PTZ camera is disabled: {camera.id}")
        if camera.host is None or not camera.host.strip():
            raise ValueError(f"PTZ camera host is not configured: {camera.id}")
        return cls(host=camera.host, port=camera.port, timeout_seconds=timeout_seconds)

    def connect(self) -> None:
        if self._socket is not None:
            self.connected = True
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout_seconds)
        self._socket = sock
        self.connected = True
        LOGGER.debug("VISCA UDP transport ready: %s:%s timeout=%.3fs", self.host, self.port, self.timeout_seconds)

    def disconnect(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self.connected = False
        LOGGER.debug("VISCA UDP transport disconnected: %s:%s", self.host, self.port)

    def send(self, packet: bytes) -> None:
        if not packet:
            raise ValueError("VISCA packet cannot be empty")
        if not self.connected or self._socket is None:
            self.connect()
        assert self._socket is not None
        LOGGER.debug("VISCA UDP send %s:%s %s", self.host, self.port, packet.hex(" "))
        try:
            self._socket.sendto(packet, (self.host, self.port))
        except OSError as exc:
            self.connected = False
            self.disconnect()
            raise ConnectionError(f"VISCA UDP send failed to {self.host}:{self.port}: {exc}") from exc
        self.sent_packets.append(packet)


@dataclass
class ReconnectSafeTransport:
    inner: PtzTransport
    reconnect_attempts: int = 1

    @property
    def connected(self) -> bool:
        return self.inner.connected

    def connect(self) -> None:
        self.inner.connect()

    def disconnect(self) -> None:
        self.inner.disconnect()

    def send(self, packet: bytes) -> None:
        try:
            self.inner.send(packet)
        except ConnectionError:
            for _ in range(self.reconnect_attempts):
                self.inner.connect()
                try:
                    self.inner.send(packet)
                    return
                except ConnectionError:
                    continue
            raise


def build_real_udp_transport(camera: PtzCamera, timeout_seconds: float = 0.5) -> ReconnectSafeTransport:
    return ReconnectSafeTransport(UdpViscaTransport.from_camera(camera, timeout_seconds=timeout_seconds))

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


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

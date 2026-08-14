from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

from .atem_control_probe import AtemManualControlClient, ATEM_TELEVISION_STUDIO_4K8_PRODUCT_NAME

LOGGER = logging.getLogger(__name__)
ATEM_DEFAULT_PORT = 9910
ATEM_4K8_DEFAULT_PORT = ATEM_DEFAULT_PORT
ATEM_MINI_PRO_DEFAULT_PORT = ATEM_DEFAULT_PORT

ATEM_4K8_SOURCE_TO_NATIVE = {
    **{f"Input {i}": i for i in range(1, 9)},
    "Black": 0,
    "MP1": 3010,
    "MP2": 3020,
    "SuperSource": 6000,
}
ATEM_4K8_NATIVE_TO_SOURCE = {value: key for key, value in ATEM_4K8_SOURCE_TO_NATIVE.items()}

ATEM_MINI_PRO_SOURCE_TO_NATIVE = {
    "Input 1": 1,
    "Input 2": 2,
    "Input 3": 3,
    "Input 4": 4,
    "STILL": 3010,
    "BLACK": 0,
}
ATEM_MINI_PRO_NATIVE_TO_SOURCE = {value: key for key, value in ATEM_MINI_PRO_SOURCE_TO_NATIVE.items()}


def logical_to_native(source_id: str) -> int:
    """Backward-compatible Television Studio 4K8 mapper."""
    try:
        return ATEM_4K8_SOURCE_TO_NATIVE[source_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported ATEM Television Studio 4K8 source: {source_id}") from exc


def native_to_logical(source_id: int | None) -> str | None:
    """Backward-compatible Television Studio 4K8 mapper."""
    if source_id is None:
        return None
    return ATEM_4K8_NATIVE_TO_SOURCE.get(source_id, f"ATEM Source {source_id}")


@dataclass
class AtemProductionClient:
    """Shared production adapter over the single existing ATEM UDP implementation.

    Model differences are confined to source/profile metadata. Session handshake,
    ACK processing, RX loop, PrgI/PrvI parsing and CPvI/DCut/DAut writes all stay
    in AtemManualControlClient.
    """

    host: str
    port: int
    source_to_native: Mapping[str, int]
    product_name_fallback: str
    model_label: str
    timeout: float = 2.0
    _client: AtemManualControlClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._native_to_source = {value: key for key, value in self.source_to_native.items()}
        self._new_session()

    def _new_session(self) -> None:
        self._client = AtemManualControlClient(self.host, self.port, timeout=self.timeout, trace_packets=False)

    @property
    def transition_state(self) -> str:
        return "in_progress" if self._client.state.transition_in_progress else "idle"

    @property
    def product_name(self) -> str:
        return self._client.state.product_name or self.product_name_fallback

    def connect(self) -> None:
        if self._client.connected:
            return
        self._client.connect()
        self._client.start_receive_loop()
        LOGGER.info(
            "ATEM initial state: program=%s preview=%s transition=%s",
            self._native_to_logical(self._client.state.program_source_id),
            self._native_to_logical(self._client.state.preview_source_id),
            self.transition_state,
        )

    def disconnect(self) -> None:
        self._client.disconnect()

    def reconnect(self) -> None:
        self.disconnect()
        self._new_session()
        self.connect()

    def poll(self) -> tuple[str | None, str | None]:
        if self._client._receiver_error is not None:
            raise RuntimeError(f"ATEM receive loop failed: {self._client._receiver_error}")
        return (
            self._native_to_logical(self._client.state.program_source_id),
            self._native_to_logical(self._client.state.preview_source_id),
        )

    def _logical_to_native(self, source_id: str) -> int:
        try:
            return self.source_to_native[source_id]
        except KeyError as exc:
            raise ValueError(f"Unsupported {self.model_label} source: {source_id}") from exc

    def _native_to_logical(self, source_id: int | None) -> str | None:
        if source_id is None:
            return None
        return self._native_to_source.get(source_id, f"ATEM Source {source_id}")

    def set_preview(self, source_id: str) -> None:
        self._client.set_preview(self._logical_to_native(source_id))

    def cut(self) -> None:
        self._client.cut()

    def auto(self) -> None:
        self._client.auto()


class AtemTelevisionStudio4K8Client(AtemProductionClient):
    def __init__(self, host: str, port: int = ATEM_4K8_DEFAULT_PORT, timeout: float = 2.0) -> None:
        super().__init__(
            host=host,
            port=port,
            source_to_native=ATEM_4K8_SOURCE_TO_NATIVE,
            product_name_fallback=ATEM_TELEVISION_STUDIO_4K8_PRODUCT_NAME,
            model_label="ATEM Television Studio 4K8",
            timeout=timeout,
        )


class AtemMiniProClient(AtemProductionClient):
    def __init__(self, host: str, port: int = ATEM_MINI_PRO_DEFAULT_PORT, timeout: float = 2.0) -> None:
        super().__init__(
            host=host,
            port=port,
            source_to_native=ATEM_MINI_PRO_SOURCE_TO_NATIVE,
            product_name_fallback="ATEM Mini Pro",
            model_label="ATEM Mini Pro",
            timeout=timeout,
        )

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .atem_control_probe import AtemManualControlClient, ATEM_TELEVISION_STUDIO_4K8_PRODUCT_NAME

LOGGER = logging.getLogger(__name__)
ATEM_4K8_DEFAULT_PORT = 9910
ATEM_4K8_SOURCE_TO_NATIVE = {**{f"Input {i}": i for i in range(1, 9)}, "Black": 0, "MP1": 3010, "MP2": 3020, "SuperSource": 6000}
ATEM_4K8_NATIVE_TO_SOURCE = {value: key for key, value in ATEM_4K8_SOURCE_TO_NATIVE.items()}


def logical_to_native(source_id: str) -> int:
    try:
        return ATEM_4K8_SOURCE_TO_NATIVE[source_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported ATEM Television Studio 4K8 source: {source_id}") from exc


def native_to_logical(source_id: int | None) -> str | None:
    if source_id is None:
        return None
    return ATEM_4K8_NATIVE_TO_SOURCE.get(source_id, f"ATEM Source {source_id}")


@dataclass
class AtemTelevisionStudio4K8Client:
    host: str
    port: int = ATEM_4K8_DEFAULT_PORT
    timeout: float = 2.0
    _client: AtemManualControlClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._new_session()

    def _new_session(self) -> None:
        self._client = AtemManualControlClient(self.host, self.port, timeout=self.timeout, trace_packets=False)

    @property
    def transition_state(self) -> str:
        return "in_progress" if self._client.state.transition_in_progress else "idle"

    @property
    def product_name(self) -> str:
        return self._client.state.product_name or ATEM_TELEVISION_STUDIO_4K8_PRODUCT_NAME

    def connect(self) -> None:
        if self._client.connected:
            return
        self._client.connect()
        self._client.start_receive_loop()
        LOGGER.info("ATEM initial state: program=%s preview=%s transition=%s",
                    native_to_logical(self._client.state.program_source_id),
                    native_to_logical(self._client.state.preview_source_id), self.transition_state)

    def disconnect(self) -> None:
        self._client.disconnect()

    def reconnect(self) -> None:
        self.disconnect()
        self._new_session()
        self.connect()

    def poll(self) -> tuple[str | None, str | None]:
        if self._client._receiver_error is not None:
            raise RuntimeError(f"ATEM receive loop failed: {self._client._receiver_error}")
        return native_to_logical(self._client.state.program_source_id), native_to_logical(self._client.state.preview_source_id)

    def set_preview(self, source_id: str) -> None:
        self._client.set_preview(logical_to_native(source_id))

    def cut(self) -> None:
        self._client.cut()

    def auto(self) -> None:
        self._client.auto()

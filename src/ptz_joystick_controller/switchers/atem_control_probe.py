from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from .atem_probe import (
    ATEM_FLAG_RELIABLE,
    AtemProbeError,
    AtemReadOnlyProbeClient,
    AtemReadOnlyState,
    AtemTimeoutError,
    encode_atem_packet,
)

ATEM_TELEVISION_STUDIO_4K8_PRODUCT_NAME = "ATEM Television Studio 4K8"
ATEM_ME1_INDEX = 0


class AtemControlError(AtemProbeError):
    """Error raised by the isolated Stage55 manual-control layer."""


class AtemCommandTimeout(AtemTimeoutError, AtemControlError):
    """A write command was sent, but matching switcher feedback did not arrive."""


@dataclass(frozen=True, slots=True)
class AtemControlCommand:
    name: str
    payload: bytes

    def encode_chunk(self) -> bytes:
        return encode_control_chunk(self.name, self.payload)


def encode_control_chunk(name: str, payload: bytes) -> bytes:
    """Encode one higher-level ATEM command chunk.

    This helper only performs command framing. Stage55 exposes only the three
    explicitly supported command constructors below to the manual-control API.
    """
    if len(name) != 4 or not name.isascii():
        raise ValueError("ATEM command name must contain four ASCII characters")
    length = 8 + len(payload)
    return length.to_bytes(2, "big") + b"\x00\x00" + name.encode("ascii") + payload


def _validate_me_index(me_index: int) -> None:
    if me_index != ATEM_ME1_INDEX:
        raise ValueError("Stage55 control supports M/E 1 only (index 0)")


def _validate_source_id(source_id: int) -> None:
    if isinstance(source_id, bool) or not isinstance(source_id, int):
        raise TypeError("ATEM source_id must be an integer")
    if not 0 <= source_id <= 0xFFFF:
        raise ValueError("ATEM source_id must be in range 0..65535")


def preview_input_command(source_id: int, *, me_index: int = ATEM_ME1_INDEX) -> AtemControlCommand:
    _validate_me_index(me_index)
    _validate_source_id(source_id)
    return AtemControlCommand("CPvI", bytes((me_index, 0)) + source_id.to_bytes(2, "big"))


def cut_command(*, me_index: int = ATEM_ME1_INDEX) -> AtemControlCommand:
    _validate_me_index(me_index)
    return AtemControlCommand("DCut", bytes((me_index, 0, 0, 0)))


def auto_command(*, me_index: int = ATEM_ME1_INDEX) -> AtemControlCommand:
    _validate_me_index(me_index)
    return AtemControlCommand("DAut", bytes((me_index, 0, 0, 0)))


class AtemManualControlClient(AtemReadOnlyProbeClient):
    """Stage55 manual-control extension of the verified Stage54 UDP session.

    It reuses the Stage54 socket, session handshake, ACK handling, receive loop,
    and state parser. The only state-changing packets it can generate are:
    CPvI (M/E 1 Preview), DCut (M/E 1 CUT), and DAut (M/E 1 AUTO).
    """

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._local_packet_id = 1

    def connect(self) -> AtemReadOnlyState:
        state = super().connect()
        self._local_packet_id = 1
        return state

    def set_preview(self, source_id: int, *, feedback_timeout: float | None = None) -> AtemReadOnlyState:
        _validate_source_id(source_id)
        if self.state.inputs and source_id not in self.state.inputs:
            raise ValueError(f"ATEM source_id {source_id} is not present in the current input table")

        self._send_control(preview_input_command(source_id))
        self._wait_for(
            lambda state: state.preview_source_id == source_id,
            timeout=feedback_timeout,
            description=f"Preview feedback for source {source_id}",
        )
        self._logger.info(
            "Preview changed: %s -> %s",
            source_id,
            self.state.source_label(source_id),
        )
        return self.state

    def cut(self, *, feedback_timeout: float | None = None) -> AtemReadOnlyState:
        before = (self.state.program_source_id, self.state.preview_source_id)
        self._send_control(cut_command())

        def cut_confirmed(state: AtemReadOnlyState) -> bool:
            if before[0] is not None and before[1] is not None:
                return state.program_source_id == before[1] and state.preview_source_id == before[0]
            return (state.program_source_id, state.preview_source_id) != before

        self._wait_for(
            cut_confirmed,
            timeout=feedback_timeout,
            description="CUT Program/Preview feedback",
        )
        return self.state

    def auto(self, *, feedback_timeout: float | None = None) -> AtemReadOnlyState:
        before = (self.state.program_source_id, self.state.preview_source_id)
        self._send_control(auto_command())

        def confirmed(state: AtemReadOnlyState) -> bool:
            if before[0] is not None and before[1] is not None:
                return state.program_source_id == before[1] and state.preview_source_id == before[0]
            return (state.program_source_id, state.preview_source_id) != before

        self._wait_for(
            confirmed,
            timeout=feedback_timeout,
            description="AUTO transition feedback",
        )
        return self.state

    def _send_control(self, command: AtemControlCommand) -> None:
        if not self.connected or not self.confirmed:
            raise AtemControlError("ATEM control client is not connected to a confirmed session")
        packet_id = self._local_packet_id
        self._local_packet_id = 1 if packet_id >= 0xFFFF else packet_id + 1
        packet = encode_atem_packet(
            flags=ATEM_FLAG_RELIABLE,
            session_id=self.session_id,
            packet_id=packet_id,
            payload=command.encode_chunk(),
        )
        self._send(packet, command.name)

    def _wait_for(
        self,
        predicate: Callable[[AtemReadOnlyState], bool],
        *,
        timeout: float | None,
        description: str,
    ) -> None:
        wait_timeout = self.timeout if timeout is None else timeout
        if wait_timeout <= 0:
            raise ValueError("feedback timeout must be positive")
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            try:
                self.receive_once()
            except AtemTimeoutError:
                continue
            if predicate(self.state):
                return
        raise AtemCommandTimeout(f"Timed out waiting for {description}")


def is_stage55_control_command(command: AtemControlCommand) -> bool:
    return command.name in {"CPvI", "DCut", "DAut"}

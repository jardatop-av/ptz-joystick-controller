from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable

from .atem_probe import (
    ATEM_FLAG_ACK,
    ATEM_FLAG_RELIABLE,
    AtemPacket,
    AtemProbeError,
    AtemProtocolError,
    AtemReadOnlyProbeClient,
    AtemReadOnlyState,
    AtemTimeoutError,
    apply_state_command,
    encode_atem_packet,
)

ATEM_TELEVISION_STUDIO_4K8_PRODUCT_NAME = "ATEM Television Studio 4K8"
ATEM_ME1_INDEX = 0


class AtemControlError(AtemProbeError):
    """Error raised by the isolated Stage55 manual-control layer."""


class AtemCommandTimeout(AtemTimeoutError, AtemControlError):
    """Base timeout for a Stage55 control command."""


class AtemTransportAckTimeout(AtemCommandTimeout):
    """The switcher did not ACK the reliable UDP command packet."""


class AtemStateFeedbackTimeout(AtemCommandTimeout):
    """Transport ACK arrived, but matching PrvI/PrgI feedback did not."""


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
        self.last_command_packet_id: int | None = None
        self.last_command_name: str | None = None
        self.last_command_transport_acked = False
        self._receiver_stop = threading.Event()
        self._receiver_thread: threading.Thread | None = None
        self._receiver_lock = threading.RLock()
        self._state_condition = threading.Condition(self._receiver_lock)
        self._ack_waiters: dict[int, threading.Event] = {}
        self._receiver_error: BaseException | None = None

    @property
    def receiver_running(self) -> bool:
        thread = self._receiver_thread
        return bool(thread and thread.is_alive() and not self._receiver_stop.is_set())

    def start_receive_loop(self) -> None:
        """Start the single background UDP reader for an interactive session."""
        if not self.connected or not self.confirmed:
            raise AtemControlError("ATEM control client is not connected to a confirmed session")
        if self.receiver_running:
            return
        self._receiver_stop.clear()
        self._receiver_error = None
        thread = threading.Thread(
            target=self._receive_loop,
            name="atem-control-rx",
            daemon=True,
        )
        self._receiver_thread = thread
        thread.start()

    def stop_receive_loop(self, *, join_timeout: float | None = None) -> None:
        self._receiver_stop.set()
        thread = self._receiver_thread
        if thread is None:
            return
        if thread is not threading.current_thread():
            thread.join(self.timeout + 0.25 if join_timeout is None else join_timeout)
        self._receiver_thread = None
        with self._state_condition:
            for event in self._ack_waiters.values():
                event.set()
            self._state_condition.notify_all()

    def disconnect(self) -> None:
        # Closing the UDP socket wakes the sole reader on real sockets.
        self._receiver_stop.set()
        super().disconnect()
        self.stop_receive_loop(join_timeout=self.timeout + 0.25)
        with self._receiver_lock:
            self._ack_waiters.clear()

    def _receive_loop(self) -> None:
        self._logger.info("ATEM RX LOOP started")
        try:
            while not self._receiver_stop.is_set() and self.connected:
                before_program = self.state.program_source_id
                before_preview = self.state.preview_source_id
                before_transition = self.state.transition_in_progress
                try:
                    packet = self._recv_packet()
                    commands = self._process_session_packet(packet, context="state")
                except AtemTimeoutError:
                    continue
                except AtemProbeError as exc:
                    if self._receiver_stop.is_set() or not self.connected:
                        break
                    self._receiver_error = exc
                    self._logger.warning("ATEM RX LOOP error: %s", exc)
                    break

                with self._state_condition:
                    for command in commands:
                        try:
                            apply_state_command(self.state, command)
                        except AtemProtocolError as exc:
                            self._logger.debug(
                                "Ignoring malformed ATEM command %s: %s",
                                command.name,
                                exc,
                            )
                    self._state_condition.notify_all()

                if self.state.preview_source_id != before_preview:
                    self._logger.info(
                        "ATEM STATE Preview changed: %s -> %s",
                        self.state.preview_source_id,
                        self.state.source_label(self.state.preview_source_id),
                    )
                if self.state.program_source_id != before_program:
                    self._logger.info(
                        "ATEM STATE Program changed: %s -> %s",
                        self.state.program_source_id,
                        self.state.source_label(self.state.program_source_id),
                    )
                if self.state.transition_in_progress != before_transition:
                    self._logger.debug(
                        "ATEM STATE transition_in_progress=%s",
                        self.state.transition_in_progress,
                    )
        finally:
            with self._state_condition:
                for event in self._ack_waiters.values():
                    event.set()
                self._state_condition.notify_all()
            self._logger.info("ATEM RX LOOP stopped")

    def _process_session_packet(
        self,
        packet: AtemPacket,
        *,
        context: str,
    ):  # type: ignore[no-untyped-def]
        commands = super()._process_session_packet(packet, context=context)
        if packet.flags & ATEM_FLAG_ACK and packet.ack_id:
            with self._receiver_lock:
                waiter = self._ack_waiters.get(packet.ack_id)
                if waiter is not None:
                    waiter.set()
        return commands

    def set_preview(self, source_id: int, *, feedback_timeout: float | None = None) -> AtemReadOnlyState:
        _validate_source_id(source_id)
        if self.state.inputs and source_id not in self.state.inputs:
            raise ValueError(f"ATEM source_id {source_id} is not present in the current input table")

        packet_id = self._send_control(preview_input_command(source_id))
        self._wait_for_transport_ack(packet_id, timeout=feedback_timeout)
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
        packet_id = self._send_control(cut_command())
        self._wait_for_transport_ack(packet_id, timeout=feedback_timeout)

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
        packet_id = self._send_control(auto_command())
        self._wait_for_transport_ack(packet_id, timeout=feedback_timeout)

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

    def _send_control(self, command: AtemControlCommand) -> int:
        if not self.connected or not self.confirmed:
            raise AtemControlError("ATEM control client is not connected to a confirmed session")
        packet_id = self.next_local_packet_id()
        self.last_command_packet_id = packet_id
        self.last_command_name = command.name
        self.last_command_transport_acked = False
        if self.receiver_running:
            with self._receiver_lock:
                self._ack_waiters[packet_id] = threading.Event()
        packet = encode_atem_packet(
            flags=ATEM_FLAG_RELIABLE,
            session_id=self.session_id,
            packet_id=packet_id,
            payload=command.encode_chunk(),
        )
        self._logger.info("ATEM COMMAND SEND packet_id=%d command=%s", packet_id, command.name)
        self._send(packet, command.name)
        return packet_id

    def _wait_for_transport_ack(self, packet_id: int, *, timeout: float | None) -> None:
        wait_timeout = self.timeout if timeout is None else timeout
        if wait_timeout <= 0:
            raise ValueError("transport ACK timeout must be positive")

        if self.receiver_running:
            with self._receiver_lock:
                event = self._ack_waiters.setdefault(packet_id, threading.Event())
                already_acked = self.consume_local_packet_ack(packet_id)
                if already_acked:
                    event.set()
            if event.wait(wait_timeout):
                if self._receiver_error is not None and not self.is_local_packet_acked(packet_id):
                    raise AtemControlError(f"ATEM receive loop failed: {self._receiver_error}")
                self.consume_local_packet_ack(packet_id)
                with self._receiver_lock:
                    self._ack_waiters.pop(packet_id, None)
                self.last_command_transport_acked = True
                self._logger.info("ATEM COMMAND ACK packet_id=%d", packet_id)
                return
            with self._receiver_lock:
                self._ack_waiters.pop(packet_id, None)
            self._logger.warning("ATEM COMMAND ACK TIMEOUT packet_id=%d", packet_id)
            raise AtemTransportAckTimeout(f"Timed out waiting for transport ACK for packet_id={packet_id}")

        # Backward-compatible synchronous path for isolated unit tests that do
        # not start the interactive receiver. Never used while the RX loop runs.
        if self.consume_local_packet_ack(packet_id):
            self.last_command_transport_acked = True
            self._logger.info("ATEM COMMAND ACK packet_id=%d", packet_id)
            return
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            try:
                self.receive_once()
            except AtemTimeoutError:
                continue
            if self.consume_local_packet_ack(packet_id):
                self.last_command_transport_acked = True
                self._logger.info("ATEM COMMAND ACK packet_id=%d", packet_id)
                return
        self._logger.warning("ATEM COMMAND ACK TIMEOUT packet_id=%d", packet_id)
        raise AtemTransportAckTimeout(f"Timed out waiting for transport ACK for packet_id={packet_id}")

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
        if predicate(self.state):
            return

        if self.receiver_running:
            deadline = time.monotonic() + wait_timeout
            with self._state_condition:
                while not predicate(self.state):
                    if self._receiver_error is not None:
                        raise AtemControlError(f"ATEM receive loop failed: {self._receiver_error}")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AtemStateFeedbackTimeout(f"Timed out waiting for {description}")
                    self._state_condition.wait(remaining)
            return

        # Backward-compatible synchronous path when no background reader exists.
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            try:
                self.receive_once()
            except AtemTimeoutError:
                continue
            if predicate(self.state):
                return
        raise AtemStateFeedbackTimeout(f"Timed out waiting for {description}")


def is_stage55_control_command(command: AtemControlCommand) -> bool:
    return command.name in {"CPvI", "DCut", "DAut"}

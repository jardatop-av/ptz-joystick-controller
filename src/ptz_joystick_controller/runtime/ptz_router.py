from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from ..app_state import AppState
from ..event_bus import Event, EventBus
from ..models.commands import EventType
from ..models.joystick_input import HatPtzStep, PtzVelocity
from ..models.ptz import PtzCamera
from ..ptz import CameraSession, FakeViscaTransport, ReconnectSafeTransport
from ..ptz.transport import PtzTransport
from ..ptz.commands import PanDirection, PanTiltCommand, TiltDirection

LOGGER = logging.getLogger(__name__)


@dataclass
class RoutedPtzSession:
    session: CameraSession
    fake_transport: FakeViscaTransport


@dataclass
class PtzRouter:
    """Route normalized joystick PTZ intent to the currently active PTZ camera.

    This router intentionally uses fake/reconnect-safe VISCA transports only. It
    is the runtime glue between AppState.active_ptz_camera_id and the existing
    offline VISCA abstraction. Real UDP/TCP sockets are not part of this layer.
    """

    state: AppState
    event_bus: EventBus
    sessions: dict[str, RoutedPtzSession] = field(init=False)
    command_log: list[str] = field(default_factory=list)
    transport_factory: Callable[[PtzCamera], PtzTransport] | None = None

    def __post_init__(self) -> None:
        self.sessions = {
            camera.id: self._build_session(camera)
            for camera in self.state.config.ptz.cameras
            if camera.enabled
        }
        self.event_bus.subscribe(EventType.PTZ_STOP_REQUESTED, self._on_stop_requested)

    def _build_session(self, camera: PtzCamera) -> RoutedPtzSession:
        fake = FakeViscaTransport()
        transport = self.transport_factory(camera) if self.transport_factory is not None else ReconnectSafeTransport(fake)
        session = CameraSession(camera=camera, transport=transport)
        return RoutedPtzSession(session=session, fake_transport=fake)

    @property
    def active_camera_id(self) -> str | None:
        return self.state.active_ptz_camera_id

    @property
    def active_session(self) -> CameraSession | None:
        camera_id = self.active_camera_id
        if camera_id is None:
            return None
        routed = self.sessions.get(camera_id)
        return routed.session if routed else None

    def camera_command_count(self, camera_id: str) -> int:
        routed = self.sessions.get(camera_id)
        if routed is None:
            return 0
        return len(routed.session.command_log)

    def transport_packets(self, camera_id: str) -> list[bytes]:
        routed = self.sessions.get(camera_id)
        if routed is None:
            return []
        return list(routed.fake_transport.sent_packets)

    def route_velocity(self, velocity: PtzVelocity) -> bool:
        session = self.active_session
        if session is None:
            LOGGER.debug(
                "PTZ disabled: preview source %s has no PTZ mapping",
                self.state.preview_source_id,
            )
            return False
        session.pan_tilt_from_axes(velocity.pan, velocity.tilt)
        session.zoom_from_axis(velocity.zoom)
        self.command_log.append(
            f"{session.camera.id}:axes pan={velocity.pan:.3f} tilt={velocity.tilt:.3f} zoom={velocity.zoom:.3f} speed={velocity.speed_multiplier:.3f}"
        )
        LOGGER.info(
            "PTZ command: camera=%s preview=%s pan=%.3f tilt=%.3f zoom=%.3f speed=%.3f",
            session.camera.id,
            self.state.preview_source_id,
            velocity.pan,
            velocity.tilt,
            velocity.zoom,
            velocity.speed_multiplier,
        )
        return True

    def route_hat_step(self, step: HatPtzStep) -> bool:
        if step.pan_speed == 0 and step.tilt_speed == 0:
            return False
        session = self.active_session
        if session is None:
            LOGGER.debug(
                "PTZ hat disabled: preview source %s has no PTZ mapping",
                self.state.preview_source_id,
            )
            return False

        pan_speed = abs(step.pan_speed)
        tilt_speed = abs(step.tilt_speed)
        pan_direction = PanDirection.STOP
        tilt_direction = TiltDirection.STOP
        if step.pan_speed > 0:
            pan_direction = PanDirection.RIGHT
        elif step.pan_speed < 0:
            pan_direction = PanDirection.LEFT
        if step.tilt_speed > 0:
            tilt_direction = TiltDirection.UP
        elif step.tilt_speed < 0:
            tilt_direction = TiltDirection.DOWN

        command = session.builder.pan_tilt(
            PanTiltCommand(
                pan_speed=pan_speed,
                tilt_speed=tilt_speed,
                pan_direction=pan_direction,
                tilt_direction=tilt_direction,
            )
        )
        session.send_command(command)
        self.command_log.append(f"{session.camera.id}:hat pan={step.pan_speed} tilt={step.tilt_speed}")
        LOGGER.info(
            "PTZ hat command: camera=%s preview=%s pan_speed=%s tilt_speed=%s",
            session.camera.id,
            self.state.preview_source_id,
            step.pan_speed,
            step.tilt_speed,
        )
        return True

    def stop(self, reason: str, camera_id: str | None = None) -> bool:
        target_id = camera_id or self.active_camera_id
        if target_id is None:
            LOGGER.debug("PTZ stop ignored: no active PTZ camera reason=%s", reason)
            return False
        routed = self.sessions.get(target_id)
        if routed is None:
            LOGGER.warning("PTZ stop requested for unknown camera: %s", target_id)
            return False
        routed.session.stop(reason=reason)
        self.command_log.append(f"{target_id}:stop reason={reason}")
        LOGGER.info("PTZ STOP: camera=%s reason=%s", target_id, reason)
        return True

    def _on_stop_requested(self, event: Event) -> None:
        self.stop(
            reason=str(event.payload.get("reason") or "runtime_stop"),
            camera_id=event.payload.get("camera_id"),
        )

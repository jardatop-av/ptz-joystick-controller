from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable

from ..app_state import AppState
from ..event_bus import Event, EventBus
from ..models.commands import EventType
from ..models.joystick_input import HatPtzStep, PtzVelocity
from ..models.ptz import PtzCamera
from ..ptz import CameraSession, FakeViscaTransport, ReconnectSafeTransport
from ..ptz.commands import PanDirection, PanTiltCommand, TiltDirection
from ..ptz.transport import PtzTransport

LOGGER = logging.getLogger(__name__)


class PanTiltSource(StrEnum):
    NONE = "none"
    MAIN = "main"
    HAT = "hat"


@dataclass(frozen=True)
class PanTiltIntent:
    source: PanTiltSource
    pan: float = 0.0
    tilt: float = 0.0
    pan_speed: int = 0
    tilt_speed: int = 0

    @property
    def moving(self) -> bool:
        if self.source == PanTiltSource.HAT:
            return self.pan_speed != 0 or self.tilt_speed != 0
        return self.pan != 0.0 or self.tilt != 0.0

    @property
    def stop_reason(self) -> str:
        if self.source == PanTiltSource.HAT:
            return "hat_center"
        return "axis_center"


@dataclass(frozen=True)
class PtzRouterDiagnostics:
    active_camera_id: str | None
    active_preview_source_id: str | None
    active_camera_moving: bool
    active_camera_last_command: str | None
    total_logged_commands: int
    pan_tilt_active: bool = False
    zoom_active: bool = False
    hat_active: bool = False
    effective_pan_tilt_source: str = PanTiltSource.NONE.value
    last_effective_pan_tilt_command: str | None = None
    last_zoom_command: str | None = None
    pan_tilt_center_samples: int = 0
    zoom_center_samples: int = 0


@dataclass
class RoutedPtzSession:
    session: CameraSession
    fake_transport: FakeViscaTransport


@dataclass
class PtzRouter:
    """Route joystick PTZ intent to the currently active PTZ camera.

    Main pan/tilt and hat pan/tilt are arbitrated into one effective pan/tilt
    command path. Zoom remains independent.
    """

    state: AppState
    event_bus: EventBus
    sessions: dict[str, RoutedPtzSession] = field(init=False)
    command_log: list[str] = field(default_factory=list)
    transport_factory: Callable[[PtzCamera], PtzTransport] | None = None
    pan_tilt_active: bool = field(default=False, init=False)
    zoom_active: bool = field(default=False, init=False)
    hat_active: bool = field(default=False, init=False)
    effective_pan_tilt_source: PanTiltSource = field(default=PanTiltSource.NONE, init=False)
    last_effective_pan_tilt_command: str | None = field(default=None, init=False)
    last_zoom_command: str | None = field(default=None, init=False)
    pan_tilt_center_samples: int = field(default=0, init=False)
    zoom_center_samples: int = field(default=0, init=False)

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


    def rebuild_sessions(self) -> None:
        """Rebuild camera sessions/transports from the current runtime config.

        Used by runtime config apply.  Movement state is cleared after a safe
        stop has been requested by the caller, so the new sessions never
        inherit stale movement flags from the previous camera set.
        """
        for routed in self.sessions.values():
            try:
                routed.session.transport.disconnect()
            except Exception:
                LOGGER.debug("PTZ transport disconnect during rebuild failed", exc_info=True)
        self.sessions = {
            camera.id: self._build_session(camera)
            for camera in self.state.config.ptz.cameras
            if camera.enabled
        }
        self.pan_tilt_active = False
        self.zoom_active = False
        self.hat_active = False
        self.effective_pan_tilt_source = PanTiltSource.NONE
        self.pan_tilt_center_samples = 0
        self.zoom_center_samples = 0
        self.command_log.append("router:rebuild_sessions")
        LOGGER.info("PTZ router sessions rebuilt from applied configuration")

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

    @property
    def pan_tilt_movement_threshold(self) -> float:
        return self.state.config.joystick.output_deadzone.pan_tilt

    @property
    def zoom_movement_threshold(self) -> float:
        return self.state.config.joystick.output_deadzone.zoom

    @property
    def center_confirm_samples(self) -> int:
        return self.state.config.ptz.stop_watchdog.center_confirm_samples

    def _publish_ptz_event(self, event_type: str, payload: dict[str, object]) -> None:
        self.event_bus.publish(event_type, payload)

    def _main_pan_tilt_is_centered(self, pan: float, tilt: float) -> bool:
        threshold = self.pan_tilt_movement_threshold
        return abs(pan) < threshold and abs(tilt) < threshold

    def _zoom_is_centered(self, zoom: float) -> bool:
        return abs(zoom) < self.zoom_movement_threshold

    def _hat_vector_from_step(self, step: HatPtzStep) -> tuple[int, int]:
        """Return the effective 2-axis HAT vector used for PTZ routing.

        The vector is stored by HatProcessor before speeds are converted to
        absolute VISCA values. Keeping x and y explicitly prevents any
        left/right asymmetry or loss of the tilt component when a diagonal is
        formed while the HAT is already held horizontally. Older tests may
        still construct HatPtzStep with speeds only, so fall back to deriving
        the vector from signed speeds when x/y are not set.
        """
        if step.x != 0 or step.y != 0:
            return step.x, step.y
        x = 0
        y = 0
        if step.pan_speed < 0:
            x = -1
        elif step.pan_speed > 0:
            x = 1
        if step.tilt_speed > 0:
            y = 1
        elif step.tilt_speed < 0:
            y = -1
        return x, y

    def _select_pan_tilt_intent(self, velocity: PtzVelocity, hat_step: HatPtzStep | None) -> PanTiltIntent:
        if not self._main_pan_tilt_is_centered(velocity.pan, velocity.tilt):
            return PanTiltIntent(PanTiltSource.MAIN, pan=velocity.pan, tilt=velocity.tilt)
        if (velocity.pan != 0.0 or velocity.tilt != 0.0) and (hat_step is None or not hat_step.moving):
            LOGGER.info(
                "PTZ PAN/TILT SUPPRESSED reason=below_threshold pan=%.3f tilt=%.3f",
                velocity.pan,
                velocity.tilt,
            )
        if hat_step is not None and hat_step.moving:
            return PanTiltIntent(
                PanTiltSource.HAT,
                pan=float(hat_step.pan_speed),
                tilt=float(hat_step.tilt_speed),
                pan_speed=hat_step.pan_speed,
                tilt_speed=hat_step.tilt_speed,
            )
        return PanTiltIntent(PanTiltSource.NONE)

    def _send_hat_pan_tilt(self, session: CameraSession, step: HatPtzStep) -> None:
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
        session.state.pan_tilt_active = True
        session.state.pan = float(step.pan_speed)
        session.state.tilt = float(step.tilt_speed)
        session.state.moving = True

    def _route_pan_tilt_intent(self, session: CameraSession, intent: PanTiltIntent) -> bool:
        if not intent.moving:
            self.pan_tilt_center_samples += 1
            if self.pan_tilt_active:
                reason = "hat_center" if self.effective_pan_tilt_source == PanTiltSource.HAT else "axis_center"
                session.stop_pan_tilt(reason=reason)
                self.command_log.append(f"{session.camera.id}:pan_tilt_stop reason={reason}")
                if reason == "hat_center":
                    self.command_log.append(f"{session.camera.id}:hat_stop reason={reason}")
                LOGGER.info("PTZ PAN/TILT STOP camera=%s reason=%s", session.camera.id, reason)
                self._publish_ptz_event("ptz.pan_tilt_stop", {"camera_id": session.camera.id, "reason": reason})
                if self.pan_tilt_center_samples >= self.center_confirm_samples:
                    self.command_log.append(f"{session.camera.id}:watchdog_pan_tilt_stop reason=center_confirmed")
                    LOGGER.info("PTZ WATCHDOG STOP camera=%s reason=center_confirmed", session.camera.id)
                self.pan_tilt_active = False
                self.hat_active = False
                self.effective_pan_tilt_source = PanTiltSource.NONE
                self.last_effective_pan_tilt_command = f"stop:{reason}"
                self.pan_tilt_center_samples = 0
                return True
            self.effective_pan_tilt_source = PanTiltSource.NONE
            self.hat_active = False
            return False

        self.pan_tilt_center_samples = 0
        if intent.source == PanTiltSource.MAIN:
            session.pan_tilt_from_axes(intent.pan, intent.tilt)
            self.pan_tilt_active = True
            self.hat_active = False
            self.effective_pan_tilt_source = PanTiltSource.MAIN
            self.last_effective_pan_tilt_command = f"main:{intent.pan:.3f}:{intent.tilt:.3f}"
            self.command_log.append(
                f"{session.camera.id}:pan_tilt source=main pan={intent.pan:.3f} tilt={intent.tilt:.3f}"
            )
            LOGGER.info(
                "PTZ PAN/TILT MOVE camera=%s source=main pan=%.3f tilt=%.3f",
                session.camera.id,
                intent.pan,
                intent.tilt,
            )
            self._publish_ptz_event(
                "ptz.pan_tilt_move",
                {"camera_id": session.camera.id, "source": "main", "pan": intent.pan, "tilt": intent.tilt},
            )
            return True

        if intent.source == PanTiltSource.HAT:
            step = HatPtzStep(
                pan_speed=intent.pan_speed,
                tilt_speed=intent.tilt_speed,
                x=-1 if intent.pan_speed < 0 else 1 if intent.pan_speed > 0 else 0,
                y=1 if intent.tilt_speed > 0 else -1 if intent.tilt_speed < 0 else 0,
            )
            self._send_hat_pan_tilt(session, step)
            x, y = self._hat_vector_from_step(step)
            self.pan_tilt_active = True
            self.hat_active = True
            self.effective_pan_tilt_source = PanTiltSource.HAT
            self.last_effective_pan_tilt_command = f"hat:{x}:{y}:{step.pan_speed}:{step.tilt_speed}"
            self.command_log.append(
                f"{session.camera.id}:pan_tilt source=hat x={x} y={y} pan={step.pan_speed} tilt={step.tilt_speed}"
            )
            # Compatibility entry for older tests; this is not a separate HAT command path.
            self.command_log.append(f"{session.camera.id}:hat pan={step.pan_speed} tilt={step.tilt_speed}")
            self.command_log.append(f"{session.camera.id}:hat x={x} y={y} pan={step.pan_speed} tilt={step.tilt_speed}")
            LOGGER.info(
                "PTZ PAN/TILT MOVE camera=%s source=hat x=%s y=%s pan_speed=%s tilt_speed=%s",
                session.camera.id,
                x,
                y,
                step.pan_speed,
                step.tilt_speed,
            )
            self._publish_ptz_event(
                "ptz.pan_tilt_move",
                {
                    "camera_id": session.camera.id,
                    "source": "hat",
                    "x": x,
                    "y": y,
                    "pan_speed": step.pan_speed,
                    "tilt_speed": step.tilt_speed,
                },
            )
            return True

        return False

    def _route_zoom_intent(self, session: CameraSession, velocity: PtzVelocity) -> bool:
        if not self._zoom_is_centered(velocity.zoom):
            self.zoom_center_samples = 0
            session.zoom_from_axis(velocity.zoom)
            self.zoom_active = True
            self.last_zoom_command = f"zoom:{velocity.zoom:.3f}"
            self.command_log.append(f"{session.camera.id}:zoom zoom={velocity.zoom:.3f} speed={velocity.speed_multiplier:.3f}")
            LOGGER.info(
                "PTZ ZOOM MOVE camera=%s zoom=%.3f speed=%.3f",
                session.camera.id,
                velocity.zoom,
                velocity.speed_multiplier,
            )
            self._publish_ptz_event(
                "ptz.zoom_move",
                {"camera_id": session.camera.id, "zoom": velocity.zoom, "speed_multiplier": velocity.speed_multiplier},
            )
            return True
        if velocity.zoom != 0.0:
            LOGGER.info("PTZ ZOOM SUPPRESSED reason=below_threshold zoom=%.3f", velocity.zoom)
        self.zoom_center_samples += 1
        if self.zoom_active:
            session.stop_zoom(reason="zoom_center")
            self.zoom_active = False
            self.last_zoom_command = "stop:zoom_center"
            self.command_log.append(f"{session.camera.id}:zoom_stop reason=zoom_center")
            LOGGER.info("PTZ ZOOM STOP camera=%s reason=zoom_center", session.camera.id)
            self._publish_ptz_event("ptz.zoom_stop", {"camera_id": session.camera.id, "reason": "zoom_center"})
            if self.zoom_center_samples >= self.center_confirm_samples:
                self.command_log.append(f"{session.camera.id}:watchdog_zoom_stop reason=center_confirmed")
                LOGGER.info("PTZ WATCHDOG STOP camera=%s reason=center_confirmed", session.camera.id)
            self.zoom_center_samples = 0
            return True
        return False

    def route_controls(self, velocity: PtzVelocity, hat_step: HatPtzStep | None = None) -> bool:
        """Route one joystick cycle with deterministic pan/tilt arbitration.

        Main pan/tilt has priority. Hat is considered only when main pan/tilt is
        centered. Zoom is processed independently in the same cycle.
        """
        intent = self._select_pan_tilt_intent(velocity, hat_step)
        session = self.active_session
        has_any_intent = intent.moving or not self._zoom_is_centered(velocity.zoom)
        if session is None:
            if has_any_intent:
                LOGGER.debug(
                    "PTZ disabled: preview source %s has no PTZ mapping",
                    self.state.preview_source_id,
                )
            self.pan_tilt_active = False
            self.zoom_active = False
            self.hat_active = False
            self.effective_pan_tilt_source = PanTiltSource.NONE
            self.pan_tilt_center_samples = 0
            self.zoom_center_samples = 0
            return False
        did_send = self._route_pan_tilt_intent(session, intent)
        did_send = self._route_zoom_intent(session, velocity) or did_send
        return did_send

    def route_velocity(self, velocity: PtzVelocity) -> bool:
        return self.route_controls(velocity, None)

    def route_hat_step(self, step: HatPtzStep) -> bool:
        # Compatibility entry point for callers/tests that route hat alone.
        return self.route_controls(PtzVelocity(), step)

    def _send_tracked_stop(self, session: CameraSession, *, reason: str) -> bool:
        sent = False
        if self.pan_tilt_active:
            session.stop_pan_tilt(reason=reason)
            sent = True
            self.command_log.append(f"{session.camera.id}:pan_tilt_stop reason={reason}")
        if self.zoom_active:
            session.stop_zoom(reason=reason)
            sent = True
            self.command_log.append(f"{session.camera.id}:zoom_stop reason={reason}")
        self.pan_tilt_active = False
        self.zoom_active = False
        self.hat_active = False
        self.effective_pan_tilt_source = PanTiltSource.NONE
        self.last_effective_pan_tilt_command = f"stop:{reason}" if sent else self.last_effective_pan_tilt_command
        self.last_zoom_command = f"stop:{reason}" if sent else self.last_zoom_command
        self.pan_tilt_center_samples = 0
        self.zoom_center_samples = 0
        return sent

    def stop_previous_camera(self, camera_id: str, *, reason: str = "preview_source_changed") -> bool:
        routed = self.sessions.get(camera_id)
        if routed is None:
            LOGGER.warning("PTZ stop requested for unknown previous camera: %s", camera_id)
            self.pan_tilt_active = False
            self.zoom_active = False
            self.hat_active = False
            self.effective_pan_tilt_source = PanTiltSource.NONE
            return False
        sent = self._send_tracked_stop(routed.session, reason=reason)
        if not sent:
            routed.session.stop_all(reason=reason)
            sent = True
        self.command_log.append(f"{camera_id}:stop_previous reason={reason}")
        if reason == "preview_source_changed":
            self.command_log.append(f"{camera_id}:stop reason=active_source_changed")
        LOGGER.info("PTZ STOP PREVIOUS CAMERA camera=%s reason=%s", camera_id, reason)
        self._publish_ptz_event("ptz.stop_previous", {"camera_id": camera_id, "reason": reason})
        return sent

    def stop_hat(self, reason: str = "hat_center") -> bool:
        # Compatibility wrapper; unified pan/tilt path owns the stop.
        session = self.active_session
        if session is None:
            self.hat_active = False
            LOGGER.debug("PTZ hat stop ignored: no active PTZ camera reason=%s", reason)
            return False
        if not self.hat_active and not self.pan_tilt_active:
            return False
        session.stop_pan_tilt(reason=reason)
        self.pan_tilt_active = False
        self.hat_active = False
        self.effective_pan_tilt_source = PanTiltSource.NONE
        self.command_log.append(f"{session.camera.id}:pan_tilt_stop reason={reason}")
        self.command_log.append(f"{session.camera.id}:hat_stop reason={reason}")
        LOGGER.info("PTZ PAN/TILT STOP camera=%s reason=%s", session.camera.id, reason)
        self._publish_ptz_event("ptz.pan_tilt_stop", {"camera_id": session.camera.id, "reason": reason})
        return True

    def recall_preset(self, preset_number: int, *, stop_before_recall: bool = True) -> bool:
        """Recall a PTZ preset on the active camera only.

        Preset recall is a discrete VISCA command. If continuous movement is
        currently tracked, the router may stop that movement before recall. It
        intentionally never sends a stop after recall, because that can cancel
        preset travel on cameras such as NewTek PTZ1.
        """
        if not 0 <= preset_number <= 255:
            raise ValueError("PTZ preset number must be in range 0..255")
        session = self.active_session
        if session is None:
            LOGGER.info(
                "PTZ preset recall ignored: no active PTZ camera preset=%s preview=%s",
                preset_number,
                self.state.preview_source_id,
            )
            self.command_log.append(f"preset_ignored preset={preset_number} reason=no_active_ptz")
            return False
        if stop_before_recall:
            stopped = self._send_tracked_stop(session, reason="before_preset_recall")
            if stopped:
                self.command_log.append(f"{session.camera.id}:stop_before_preset_recall")
                LOGGER.info("PTZ STOP BEFORE PRESET RECALL camera=%s", session.camera.id)
        session.recall_preset(preset_number)
        self.command_log.append(f"{session.camera.id}:preset_recall preset={preset_number}")
        LOGGER.info("PTZ PRESET RECALL camera=%s preset=%s", session.camera.id, preset_number)
        self._publish_ptz_event("ptz.preset_recall", {"camera_id": session.camera.id, "preset": preset_number})
        return True

    def stop(self, reason: str, camera_id: str | None = None) -> bool:
        target_id = camera_id or self.active_camera_id
        if target_id is None:
            LOGGER.debug("PTZ stop ignored: no active PTZ camera reason=%s", reason)
            self.pan_tilt_active = False
            self.zoom_active = False
            self.hat_active = False
            self.effective_pan_tilt_source = PanTiltSource.NONE
            return False
        routed = self.sessions.get(target_id)
        if routed is None:
            LOGGER.warning("PTZ stop requested for unknown camera: %s", target_id)
            return False
        if reason == "preview_source_changed":
            return self.stop_previous_camera(target_id, reason=reason)
        routed.session.stop_all(reason=reason)
        if target_id == self.active_camera_id:
            self.pan_tilt_active = False
            self.zoom_active = False
            self.hat_active = False
            self.effective_pan_tilt_source = PanTiltSource.NONE
        self.command_log.append(f"{target_id}:stop reason={reason}")
        LOGGER.info("PTZ STOP: camera=%s reason=%s", target_id, reason)
        self._publish_ptz_event("ptz.stop", {"camera_id": target_id, "reason": reason})
        return True

    def stop_all_active_motion(self, reason: str = "script_exit") -> bool:
        session = self.active_session
        if session is None:
            return False
        session.stop_pan_tilt(reason=reason)
        session.stop_zoom(reason=reason)
        self.pan_tilt_active = False
        self.zoom_active = False
        self.hat_active = False
        self.effective_pan_tilt_source = PanTiltSource.NONE
        self.last_effective_pan_tilt_command = f"stop:{reason}"
        self.last_zoom_command = f"stop:{reason}"
        self.command_log.append(f"{session.camera.id}:pan_tilt_stop reason={reason}")
        self.command_log.append(f"{session.camera.id}:zoom_stop reason={reason}")
        self.command_log.append(f"{session.camera.id}:stop reason={reason}")
        LOGGER.info("PTZ PAN/TILT STOP camera=%s reason=%s", session.camera.id, reason)
        LOGGER.info("PTZ ZOOM STOP camera=%s reason=%s", session.camera.id, reason)
        self._publish_ptz_event("ptz.stop_all_active_motion", {"camera_id": session.camera.id, "reason": reason})
        return True

    def diagnostics(self) -> PtzRouterDiagnostics:
        session = self.active_session
        return PtzRouterDiagnostics(
            active_camera_id=self.active_camera_id,
            active_preview_source_id=self.state.preview_source_id,
            active_camera_moving=session.state.moving if session is not None else False,
            active_camera_last_command=session.state.last_command if session is not None else None,
            total_logged_commands=len(self.command_log),
            pan_tilt_active=self.pan_tilt_active,
            zoom_active=self.zoom_active,
            hat_active=self.hat_active,
            effective_pan_tilt_source=self.effective_pan_tilt_source.value,
            last_effective_pan_tilt_command=self.last_effective_pan_tilt_command,
            last_zoom_command=self.last_zoom_command,
            pan_tilt_center_samples=self.pan_tilt_center_samples,
            zoom_center_samples=self.zoom_center_samples,
        )

    def _on_stop_requested(self, event: Event) -> None:
        self.stop(
            reason=str(event.payload.get("reason") or "runtime_stop"),
            camera_id=event.payload.get("camera_id"),
        )

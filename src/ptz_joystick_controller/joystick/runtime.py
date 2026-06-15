from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from ..config import ControllerConfig
from ..event_bus import EventBus
from ..models.joystick_input import JoystickSnapshot, NormalizedAxisState
from ..models.joystick_runtime import JoystickDeviceInfo, JoystickHealth
from .calibration import JoystickCalibration
from .deadzone import DeadzoneProcessor
from .device import JoystickInputProvider
from .discovery import AutoJoystickDiscovery, JoystickDiscoveryBackend
from .axis_metadata import AxisInversionMetadataRegistry
from .hat import HatProcessor
from .ptz_speed import PtzSpeedScaler
from .throttle import ThrottleScaler

LOGGER = logging.getLogger(__name__)
ProviderFactory = Callable[[JoystickDeviceInfo], JoystickInputProvider]


@dataclass
class JoystickRuntimeMonitor:
    config: ControllerConfig
    event_bus: EventBus
    discovery: JoystickDiscoveryBackend = field(default_factory=AutoJoystickDiscovery)
    provider_factory: ProviderFactory | None = None
    calibration: JoystickCalibration = field(default_factory=JoystickCalibration)
    health: JoystickHealth = field(default_factory=JoystickHealth)
    provider: JoystickInputProvider | None = None

    def start(self) -> None:
        self.log_axis_inversion_settings()
        self.reconnect_if_needed()

    def log_axis_inversion_settings(self) -> None:
        registry = AxisInversionMetadataRegistry(self.config.joystick.invert)
        LOGGER.info(
            "Joystick axis inversion: pan=%s tilt=%s zoom=%s",
            self.config.joystick.invert.pan,
            self.config.joystick.invert.tilt,
            self.config.joystick.invert.zoom,
        )
        LOGGER.debug(
            "Joystick axis inversion metadata: %s",
            {key: metadata.label for key, metadata in registry.all_metadata().items()},
        )

    def _mark_disconnected_once(self, reason: str) -> None:
        was_connected = self.health.connected
        already_disconnected = self.health.state.value == "disconnected"
        if was_connected or not already_disconnected:
            LOGGER.info("Joystick disconnected: %s", reason)
            self.health.mark_disconnected(reason)
            LOGGER.info("Joystick health status: %s", self.health.status_text())
            self.event_bus.publish("joystick.disconnected", {"reason": self.health.last_error})
        else:
            # Keep reconnect attempts observable internally without flooding the runtime event stream.
            self.health.mark_disconnected(reason)

    def reconnect_if_needed(self) -> None:
        if self.provider is not None and self.health.connected:
            return
        LOGGER.info("Joystick reconnect polling...")
        previous_device = self.health.device
        was_previously_connected = self.health.connected
        devices = self.discovery.discover()
        if not devices:
            self._mark_disconnected_once("No joystick device found")
            return
        if self.provider_factory is None:
            self._mark_disconnected_once("No joystick provider factory configured")
            return
        device = devices[0]
        try:
            self.provider = self.provider_factory(device)
            snapshot = self.provider.snapshot()
            self.health.mark_connected(device, snapshot)
            if previous_device is not None or not was_previously_connected:
                LOGGER.info("Joystick reconnected: %s", device.name)
            else:
                LOGGER.info("Joystick connected: %s", device.name)
            LOGGER.info("Joystick health status: %s", self.health.status_text())
            self.event_bus.publish("joystick.connected", {"device": device, "device_name": device.name})
        except Exception as exc:  # provider failure must not crash app startup
            LOGGER.debug("Joystick reconnect failed", exc_info=True)
            self.provider = None
            self.health.mark_error(str(exc))
            LOGGER.info("Joystick disconnected: %s", exc)
            LOGGER.info("Joystick health status: %s", self.health.status_text())
            self.event_bus.publish("joystick.error", {"error": str(exc)})

    def poll(self) -> JoystickSnapshot | None:
        if self.provider is None:
            self.reconnect_if_needed()
            if self.provider is None:
                return None
        try:
            snapshot = self.provider.snapshot()
            self.health.update_snapshot(snapshot)
            self.event_bus.publish("joystick.snapshot", {"snapshot": snapshot})
            return snapshot
        except Exception as exc:
            LOGGER.debug("Joystick poll failed", exc_info=True)
            self.provider = None
            self._mark_disconnected_once(str(exc))
            return None

    def normalized_axes(self, snapshot: JoystickSnapshot) -> NormalizedAxisState:
        axes = self.calibration.normalize_axes(snapshot.axes)
        axes = self._apply_physical_axis_conventions(axes)
        return DeadzoneProcessor(self.config.joystick.deadzone).process(axes)

    def _apply_physical_axis_conventions(self, axes: NormalizedAxisState) -> NormalizedAxisState:
        """Return normalized axes using project-level physical joystick conventions.

        Most real joystick backends report the Y axis as negative when the stick
        is pushed forward/up. The rest of the PTZ pipeline uses positive tilt as
        the physical forward/up joystick intent and then applies
        ``joystick.invert.tilt`` exactly once in :class:`PtzSpeedScaler`.

        Keeping this conversion in the runtime monitor ensures the same main
        tilt inversion path is used by the manual joystick monitor, the
        joystick-to-vMix bridge and the joystick-to-vMix-PTZ end-to-end tool.
        Pan, zoom and throttle are intentionally left unchanged.
        """

        return NormalizedAxisState(
            pan=axes.pan,
            tilt=-axes.tilt,
            zoom=axes.zoom,
            throttle=axes.throttle,
        )

    def ptz_velocity(self, snapshot: JoystickSnapshot):
        axes = self.normalized_axes(snapshot)
        scaler = PtzSpeedScaler(
            invert=self.config.joystick.invert,
            throttle=ThrottleScaler(self.config.joystick.throttle),
        )
        return scaler.velocity_from_axes(axes)

    def hat_step(self, snapshot: JoystickSnapshot):
        throttle_multiplier = 1.0
        if self.config.joystick.hat.apply_throttle:
            axes = self.normalized_axes(snapshot)
            throttle_multiplier = ThrottleScaler(self.config.joystick.throttle).scale(axes.throttle)
        return HatProcessor(self.config.joystick.hat, invert=self.config.joystick.invert).to_ptz_step(
            snapshot.hat,
            throttle_multiplier=throttle_multiplier,
        )

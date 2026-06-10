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
        self.reconnect_if_needed()

    def reconnect_if_needed(self) -> None:
        if self.provider is not None and self.health.connected:
            return
        previous_device = self.health.device
        devices = self.discovery.discover()
        if not devices:
            reason = "No joystick device found"
            if self.health.connected or self.health.last_error != reason:
                LOGGER.info("Joystick disconnected: %s", reason)
            self.health.mark_disconnected(reason)
            self.event_bus.publish("joystick.disconnected", {"reason": self.health.last_error})
            return
        if self.provider_factory is None:
            reason = "No joystick provider factory configured"
            if self.health.connected or self.health.last_error != reason:
                LOGGER.info("Joystick disconnected: %s", reason)
            self.health.mark_disconnected(reason)
            self.event_bus.publish("joystick.disconnected", {"reason": self.health.last_error})
            return
        device = devices[0]
        try:
            self.provider = self.provider_factory(device)
            snapshot = self.provider.snapshot()
            self.health.mark_connected(device, snapshot)
            if previous_device != device:
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
            self.health.mark_disconnected(str(exc))
            LOGGER.info("Joystick disconnected: %s", exc)
            LOGGER.info("Joystick health status: %s", self.health.status_text())
            self.event_bus.publish("joystick.disconnected", {"reason": str(exc)})
            return None

    def normalized_axes(self, snapshot: JoystickSnapshot) -> NormalizedAxisState:
        axes = self.calibration.normalize_axes(snapshot.axes)
        return DeadzoneProcessor(self.config.joystick.deadzone).process(axes)

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
        return HatProcessor(self.config.joystick.hat).to_ptz_step(
            snapshot.hat,
            throttle_multiplier=throttle_multiplier,
        )

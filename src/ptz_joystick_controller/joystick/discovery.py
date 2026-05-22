from __future__ import annotations

import logging
import platform
from dataclasses import dataclass
from typing import Protocol

from ..models.joystick_runtime import JoystickDeviceInfo

LOGGER = logging.getLogger(__name__)

LOGITECH_EXTREME_3D_PRO_VENDOR_ID = 0x046D
LOGITECH_EXTREME_3D_PRO_PRODUCT_IDS = {0xC215, 0xC214}


class JoystickDiscoveryBackend(Protocol):
    def discover(self) -> list[JoystickDeviceInfo]: ...


@dataclass(frozen=True)
class StaticJoystickDiscovery:
    devices: tuple[JoystickDeviceInfo, ...] = ()

    def discover(self) -> list[JoystickDeviceInfo]:
        return list(self.devices)


class LinuxEvdevJoystickDiscovery:
    def discover(self) -> list[JoystickDeviceInfo]:
        try:
            from evdev import InputDevice, list_devices  # type: ignore[import-not-found]
        except ImportError:
            LOGGER.debug("evdev is not installed; Linux joystick discovery disabled")
            return []

        devices: list[JoystickDeviceInfo] = []
        for path in list_devices():
            try:
                dev = InputDevice(path)
                info = dev.info
                if info.vendor == LOGITECH_EXTREME_3D_PRO_VENDOR_ID and info.product in LOGITECH_EXTREME_3D_PRO_PRODUCT_IDS:
                    devices.append(
                        JoystickDeviceInfo(
                            name=dev.name,
                            path=path,
                            backend="evdev",
                            vendor_id=info.vendor,
                            product_id=info.product,
                        )
                    )
            except OSError as exc:
                LOGGER.debug("Skipping evdev device %s: %s", path, exc)
        return devices


class WindowsPygameJoystickDiscovery:
    def discover(self) -> list[JoystickDeviceInfo]:
        try:
            import pygame  # type: ignore[import-not-found]
        except ImportError:
            LOGGER.debug("pygame is not installed; Windows joystick discovery disabled")
            return []

        pygame.init()
        pygame.joystick.init()
        devices: list[JoystickDeviceInfo] = []
        for index in range(pygame.joystick.get_count()):
            joy = pygame.joystick.Joystick(index)
            joy.init()
            name = joy.get_name()
            if "Logitech" in name or "Extreme 3D" in name:
                devices.append(JoystickDeviceInfo(name=name, path=str(index), backend="pygame"))
        return devices


class AutoJoystickDiscovery:
    def __init__(self) -> None:
        system = platform.system().lower()
        if system == "linux":
            self._backends: list[JoystickDiscoveryBackend] = [LinuxEvdevJoystickDiscovery()]
        elif system == "windows":
            self._backends = [WindowsPygameJoystickDiscovery()]
        else:
            self._backends = []

    def discover(self) -> list[JoystickDeviceInfo]:
        devices: list[JoystickDeviceInfo] = []
        for backend in self._backends:
            devices.extend(backend.discover())
        return devices

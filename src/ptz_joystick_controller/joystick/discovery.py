from __future__ import annotations

import logging
import platform
from dataclasses import dataclass
from typing import Any, Protocol

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
    """Discover Windows joysticks through pygame with reconnect-safe refresh.

    After a USB unplug/replug cycle pygame may keep stale joystick objects or
    counts until the joystick subsystem is reinitialized. Discovery is the
    reconnect boundary, so every scan refreshes pygame's joystick subsystem and
    creates fresh Joystick(index) objects.
    """

    def __init__(self, pygame_module: Any | None = None, *, reinitialize_each_scan: bool = True) -> None:
        self._pygame_module = pygame_module
        self._reinitialize_each_scan = reinitialize_each_scan

    def _load_pygame(self) -> Any | None:
        if self._pygame_module is not None:
            return self._pygame_module
        try:
            import pygame  # type: ignore[import-not-found]
        except ImportError:
            LOGGER.debug("pygame is not installed; Windows joystick discovery disabled")
            return None
        return pygame

    def _refresh_pygame_joystick_subsystem(self, pygame: Any) -> None:
        try:
            pygame.init()
        except Exception:
            LOGGER.debug("pygame.init() failed during joystick discovery", exc_info=True)
        event = getattr(pygame, "event", None)
        if event is not None:
            pump = getattr(event, "pump", None)
            if pump is not None:
                try:
                    pump()
                except Exception:
                    LOGGER.debug("pygame.event.pump() failed during joystick discovery", exc_info=True)
        if self._reinitialize_each_scan:
            quit_joystick = getattr(pygame.joystick, "quit", None)
            if quit_joystick is not None:
                try:
                    quit_joystick()
                except Exception:
                    LOGGER.debug("pygame.joystick.quit() failed during joystick discovery", exc_info=True)
        try:
            pygame.joystick.init()
        except Exception:
            LOGGER.debug("pygame.joystick.init() failed during joystick discovery", exc_info=True)
        if event is not None:
            clear = getattr(event, "clear", None)
            if clear is not None:
                try:
                    clear()
                except Exception:
                    LOGGER.debug("pygame.event.clear() failed during joystick discovery", exc_info=True)

    def discover(self) -> list[JoystickDeviceInfo]:
        pygame = self._load_pygame()
        if pygame is None:
            return []

        self._refresh_pygame_joystick_subsystem(pygame)
        devices: list[JoystickDeviceInfo] = []
        try:
            count = int(pygame.joystick.get_count())
        except Exception:
            LOGGER.debug("pygame.joystick.get_count() failed during joystick discovery", exc_info=True)
            return []
        for index in range(count):
            try:
                joy = pygame.joystick.Joystick(index)
                joy.init()
                name = joy.get_name()
            except Exception:
                LOGGER.debug("Skipping pygame joystick index %s", index, exc_info=True)
                continue
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

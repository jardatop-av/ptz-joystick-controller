from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..models.joystick_input import ButtonEvent, HatState, JoystickSnapshot, RawAxisState
from .device import JoystickInputProvider


class WindowsPygameJoystickProvider(JoystickInputProvider):
    """Windows pygame joystick provider.

    Pygame is optional and imported lazily. This class keeps the same provider
    contract as the offline fake provider.

    Windows/pygame can keep a stale ``Joystick`` object alive after a USB
    device is unplugged. To avoid publishing stale snapshots, every public poll
    pumps pygame events, handles ``JOYDEVICEREMOVED`` and validates that the
    current device still appears in pygame's joystick list before reading axes.
    """

    BUTTON_NAMES = {
        0: "trigger",
        1: "thumb",
        2: "button_3",
        3: "button_4",
        4: "button_5",
        5: "button_6",
        6: "button_7",
        7: "button_8",
        8: "button_9",
        9: "button_10",
        10: "button_11",
        11: "button_12",
    }

    def __init__(self, index: int = 0, pygame_module: Any | None = None) -> None:
        if pygame_module is None:
            try:
                import pygame  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("pygame support requires the optional 'pygame' package") from exc
        else:
            pygame = pygame_module
        self._pygame = pygame
        self._index = index
        pygame.init()
        pygame.joystick.init()
        if index >= pygame.joystick.get_count():
            raise RuntimeError(f"pygame joystick index {index} is not available")
        self._joystick = pygame.joystick.Joystick(index)
        self._joystick.init()
        self._instance_id = self._get_instance_id(self._joystick)
        self._name = self._get_device_name(self._joystick)
        self._disconnected = False
        self._disconnect_reason: str | None = None
        self._pressed_buttons: set[str] = set()
        self._events: list[ButtonEvent] = []

    def _axis_to_raw(self, value: float) -> int:
        return int(max(-1.0, min(1.0, value)) * 32767)

    def _get_instance_id(self, joystick: Any) -> int | None:
        get_instance_id = getattr(joystick, "get_instance_id", None)
        if get_instance_id is None:
            return None
        try:
            return int(get_instance_id())
        except Exception:
            return None

    def _get_device_name(self, joystick: Any) -> str | None:
        get_name = getattr(joystick, "get_name", None)
        if get_name is None:
            return None
        try:
            return str(get_name())
        except Exception:
            return None

    def _mark_disconnected(self, reason: str) -> None:
        self._disconnected = True
        self._disconnect_reason = reason
        self._pressed_buttons.clear()
        self._events.clear()

    def _raise_if_disconnected(self) -> None:
        if self._disconnected:
            raise RuntimeError(self._disconnect_reason or "Joystick disconnected")

    def _event_matches_current_joystick(self, event: Any) -> bool:
        event_instance_id = getattr(event, "instance_id", None)
        if event_instance_id is not None and self._instance_id is not None:
            return int(event_instance_id) == self._instance_id
        # Older pygame versions may not expose instance_id. In that case treat
        # any removal event as relevant for a single active provider.
        return event_instance_id is None

    def _poll_events(self) -> None:
        pygame = self._pygame
        pump = getattr(pygame.event, "pump", None)
        if pump is not None:
            pump()
        for event in pygame.event.get():
            if event.type == pygame.JOYDEVICEREMOVED and self._event_matches_current_joystick(event):
                self._mark_disconnected("Joystick disconnected")
                continue
            if event.type not in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
                continue
            button = self.BUTTON_NAMES.get(int(event.button))
            if button is None:
                continue
            pressed = event.type == pygame.JOYBUTTONDOWN
            if pressed:
                self._pressed_buttons.add(button)
            else:
                self._pressed_buttons.discard(button)
            self._events.append(ButtonEvent(button_name=button, pressed=pressed))
        self._validate_device_available()
        self._raise_if_disconnected()

    def _validate_device_available(self) -> None:
        pygame = self._pygame
        try:
            count = int(pygame.joystick.get_count())
        except Exception as exc:
            self._mark_disconnected(f"Joystick disconnected: {exc}")
            return
        if count <= 0:
            self._mark_disconnected("Joystick disconnected")
            return
        if self._instance_id is None:
            if self._index >= count:
                self._mark_disconnected("Joystick disconnected")
            return
        for index in range(count):
            try:
                joystick = pygame.joystick.Joystick(index)
                instance_id = self._get_instance_id(joystick)
            except Exception:
                continue
            if instance_id == self._instance_id:
                return
        self._mark_disconnected("Joystick disconnected")

    def snapshot(self) -> JoystickSnapshot:
        self._poll_events()
        axes = RawAxisState(
            pan=self._axis_to_raw(self._joystick.get_axis(0)) if self._joystick.get_numaxes() > 0 else 0,
            tilt=self._axis_to_raw(self._joystick.get_axis(1)) if self._joystick.get_numaxes() > 1 else 0,
            zoom=self._axis_to_raw(self._joystick.get_axis(2)) if self._joystick.get_numaxes() > 2 else 0,
            throttle=self._axis_to_raw(self._joystick.get_axis(3)) if self._joystick.get_numaxes() > 3 else 0,
        )
        hat = HatState()
        if self._joystick.get_numhats() > 0:
            x, y = self._joystick.get_hat(0)
            hat = HatState(x=int(x), y=-int(y))
        return JoystickSnapshot(axes=axes, hat=hat, pressed_buttons=frozenset(self._pressed_buttons))

    def button_events(self) -> Iterable[ButtonEvent]:
        self._poll_events()
        events = tuple(self._events)
        self._events.clear()
        return events

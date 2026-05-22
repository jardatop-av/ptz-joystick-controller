from __future__ import annotations

from collections.abc import Iterable

from ..models.joystick_input import ButtonEvent, HatState, JoystickSnapshot, RawAxisState
from .device import JoystickInputProvider


class WindowsPygameJoystickProvider(JoystickInputProvider):
    """Windows pygame joystick provider.

    Pygame is optional and imported lazily. This class keeps the same provider
    contract as the offline fake provider.
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

    def __init__(self, index: int = 0) -> None:
        try:
            import pygame  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("pygame support requires the optional 'pygame' package") from exc
        self._pygame = pygame
        pygame.init()
        pygame.joystick.init()
        if index >= pygame.joystick.get_count():
            raise RuntimeError(f"pygame joystick index {index} is not available")
        self._joystick = pygame.joystick.Joystick(index)
        self._joystick.init()
        self._pressed_buttons: set[str] = set()
        self._events: list[ButtonEvent] = []

    def _axis_to_raw(self, value: float) -> int:
        return int(max(-1.0, min(1.0, value)) * 32767)

    def _poll_events(self) -> None:
        pygame = self._pygame
        for event in pygame.event.get():
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

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..models.joystick_input import ButtonEvent, HatState, JoystickSnapshot, RawAxisState


class JoystickInputProvider(ABC):
    @abstractmethod
    def snapshot(self) -> JoystickSnapshot:
        raise NotImplementedError

    @abstractmethod
    def button_events(self) -> Iterable[ButtonEvent]:
        raise NotImplementedError


class FakeJoystickInputProvider(JoystickInputProvider):
    def __init__(self, snapshot: JoystickSnapshot | None = None) -> None:
        self._snapshot = snapshot or JoystickSnapshot()
        self._events: list[ButtonEvent] = []

    def set_axes(self, axes: RawAxisState) -> None:
        self._snapshot = JoystickSnapshot(axes=axes, hat=self._snapshot.hat, pressed_buttons=self._snapshot.pressed_buttons)

    def set_snapshot(self, snapshot: JoystickSnapshot) -> None:
        self._snapshot = snapshot

    def set_hat(self, hat: HatState) -> None:
        self._snapshot = JoystickSnapshot(axes=self._snapshot.axes, hat=hat, pressed_buttons=self._snapshot.pressed_buttons)

    def press(self, button_name: str) -> None:
        self._events.append(ButtonEvent(button_name=button_name, pressed=True))
        self._snapshot = JoystickSnapshot(
            axes=self._snapshot.axes,
            hat=self._snapshot.hat,
            pressed_buttons=frozenset({*self._snapshot.pressed_buttons, button_name}),
        )

    def release(self, button_name: str) -> None:
        self._events.append(ButtonEvent(button_name=button_name, pressed=False))
        pressed = set(self._snapshot.pressed_buttons)
        pressed.discard(button_name)
        self._snapshot = JoystickSnapshot(
            axes=self._snapshot.axes,
            hat=self._snapshot.hat,
            pressed_buttons=frozenset(pressed),
        )

    def snapshot(self) -> JoystickSnapshot:
        return self._snapshot

    def button_events(self) -> Iterable[ButtonEvent]:
        events = tuple(self._events)
        self._events.clear()
        return events

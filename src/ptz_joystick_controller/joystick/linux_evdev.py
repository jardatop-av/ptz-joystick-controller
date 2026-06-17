from __future__ import annotations

import errno
import logging
from collections.abc import Iterable

from ..models.joystick_input import ButtonEvent, HatState, JoystickSnapshot, RawAxisState
from .device import JoystickInputProvider

LOGGER = logging.getLogger(__name__)


class LinuxEvdevJoystickProvider(JoystickInputProvider):
    """Linux evdev provider for Logitech Extreme 3D Pro.

    Imports evdev lazily so the application can start without the optional package
    and without a joystick attached.
    """

    AXIS_CODES = {
        "ABS_X": "pan",
        "ABS_Y": "tilt",
        "ABS_RZ": "zoom",
        "ABS_THROTTLE": "throttle",
    }
    HAT_X_CODE = "ABS_HAT0X"
    HAT_Y_CODE = "ABS_HAT0Y"

    BUTTON_CODES = {
        "BTN_TRIGGER": "trigger",
        "BTN_THUMB": "thumb",
        "BTN_THUMB2": "button_3",
        "BTN_TOP": "button_4",
        "BTN_TOP2": "button_5",
        "BTN_PINKIE": "button_6",
        "BTN_BASE": "button_7",
        "BTN_BASE2": "button_8",
        "BTN_BASE3": "button_9",
        "BTN_BASE4": "button_10",
        "BTN_BASE5": "button_11",
        "BTN_BASE6": "button_12",
    }

    def __init__(self, path: str) -> None:
        try:
            from evdev import InputDevice, categorize, ecodes  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("evdev support requires the optional 'evdev' package") from exc
        self._evdev_categorize = categorize
        self._ecodes = ecodes
        self._device = InputDevice(path)
        self._axes = RawAxisState()
        self._hat = HatState()
        self._pressed_buttons: set[str] = set()
        self._events: list[ButtonEvent] = []
        self._no_event_debug_logged = False
        self._initialize_state()

    def _initialize_state(self) -> None:
        try:
            abs_state = self._device.active_keys(verbose=False)
            for key_code in abs_state:
                name = self._code_name(key_code)
                button = self.BUTTON_CODES.get(name)
                if button:
                    self._pressed_buttons.add(button)
        except OSError as exc:
            LOGGER.debug("Could not initialize evdev button state: %s", exc)

    def _code_name(self, code: int) -> str:
        name = self._ecodes.bytype.get(self._ecodes.EV_KEY, {}).get(code)
        if isinstance(name, list):
            return str(name[0])
        if name:
            return str(name)
        abs_name = self._ecodes.bytype.get(self._ecodes.EV_ABS, {}).get(code)
        if isinstance(abs_name, list):
            return str(abs_name[0])
        return str(abs_name or code)

    def _set_axis(self, field_name: str, value: int) -> None:
        self._axes = RawAxisState(
            pan=value if field_name == "pan" else self._axes.pan,
            tilt=value if field_name == "tilt" else self._axes.tilt,
            zoom=value if field_name == "zoom" else self._axes.zoom,
            throttle=value if field_name == "throttle" else self._axes.throttle,
        )

    def poll(self) -> None:
        try:
            events = self._device.read()
        except BlockingIOError as exc:
            # evdev InputDevice objects are opened non-blocking. When there are
            # no pending input events, Linux raises EAGAIN/EWOULDBLOCK. This is
            # an idle joystick, not a disconnect. Keep the last known state so
            # snapshot() remains stable while the stick is untouched.
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, None):
                if not self._no_event_debug_logged:
                    LOGGER.debug("No evdev joystick events available; keeping last known state")
                    self._no_event_debug_logged = True
                return
            raise RuntimeError(f"evdev joystick read failed: {exc}") from exc
        except OSError as exc:
            raise RuntimeError(f"evdev joystick read failed: {exc}") from exc

        self._no_event_debug_logged = False
        for event in events:
            if event.type == self._ecodes.EV_ABS:
                name = self._code_name(event.code)
                if name in self.AXIS_CODES:
                    self._set_axis(self.AXIS_CODES[name], int(event.value))
                elif name == self.HAT_X_CODE:
                    self._hat = HatState(x=int(event.value), y=self._hat.y)
                elif name == self.HAT_Y_CODE:
                    self._hat = HatState(x=self._hat.x, y=int(event.value))
            elif event.type == self._ecodes.EV_KEY:
                name = self._code_name(event.code)
                button = self.BUTTON_CODES.get(name)
                if button is None:
                    continue
                pressed = bool(event.value)
                if pressed:
                    self._pressed_buttons.add(button)
                else:
                    self._pressed_buttons.discard(button)
                self._events.append(ButtonEvent(button_name=button, pressed=pressed))

    def snapshot(self) -> JoystickSnapshot:
        self.poll()
        return JoystickSnapshot(axes=self._axes, hat=self._hat, pressed_buttons=frozenset(self._pressed_buttons))

    def button_events(self) -> Iterable[ButtonEvent]:
        self.poll()
        events = tuple(self._events)
        self._events.clear()
        return events

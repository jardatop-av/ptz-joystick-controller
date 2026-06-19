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
    # Numeric fallbacks are required on Raspberry Pi / Linux because some
    # evdev installations expose ABS event codes more reliably than names,
    # and _code_name() must not accidentally resolve ABS code 0/1/5 through
    # the EV_KEY table first. Logitech Extreme 3D Pro observed mapping:
    #   ABS_X/code 0  -> pan
    #   ABS_Y/code 1  -> tilt
    #   ABS_RZ/code 5 -> twist/zoom
    #   ABS_THROTTLE/code 6 -> throttle
    AXIS_NUMERIC_CODES = {
        0: "pan",
        1: "tilt",
        5: "zoom",
        6: "throttle",
    }
    HAT_X_CODE = "ABS_HAT0X"
    HAT_Y_CODE = "ABS_HAT0Y"
    HAT_X_NUMERIC_CODE = 16
    HAT_Y_NUMERIC_CODE = 17

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
        self._unknown_abs_debug_logged: set[int] = set()
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

    def _key_code_name(self, code: int) -> str:
        name = self._ecodes.bytype.get(self._ecodes.EV_KEY, {}).get(code)
        if isinstance(name, list):
            return str(name[0])
        return str(name or code)

    def _abs_code_name(self, code: int) -> str:
        name = self._ecodes.bytype.get(self._ecodes.EV_ABS, {}).get(code)
        if isinstance(name, list):
            return str(name[0])
        return str(name or code)

    def _code_name(self, code: int) -> str:
        # Backward-compatible helper for callers that do not know the event
        # type. Prefer _key_code_name() or _abs_code_name() in event handling.
        name = self._ecodes.bytype.get(self._ecodes.EV_KEY, {}).get(code)
        if isinstance(name, list):
            return str(name[0])
        if name:
            return str(name)
        return self._abs_code_name(code)

    def _set_axis(self, field_name: str, value: int) -> None:
        self._axes = RawAxisState(
            pan=value if field_name == "pan" else self._axes.pan,
            tilt=value if field_name == "tilt" else self._axes.tilt,
            zoom=value if field_name == "zoom" else self._axes.zoom,
            throttle=value if field_name == "throttle" else self._axes.throttle,
        )

    def poll(self) -> None:
        try:
            # evdev InputDevice.read() returns an iterator-like object. With
            # non-blocking devices the EAGAIN/BlockingIOError may be raised
            # lazily while iterating, not when read() is called. Force the
            # iteration inside this try block so an idle joystick is handled as
            # "no events available" rather than as a disconnect.
            events = tuple(self._device.read())
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
                name = self._abs_code_name(event.code)
                axis = self.AXIS_NUMERIC_CODES.get(int(event.code)) or self.AXIS_CODES.get(name)
                if axis is not None:
                    self._set_axis(axis, int(event.value))
                elif int(event.code) == self.HAT_X_NUMERIC_CODE or name == self.HAT_X_CODE:
                    self._hat = HatState(x=int(event.value), y=self._hat.y)
                elif int(event.code) == self.HAT_Y_NUMERIC_CODE or name == self.HAT_Y_CODE:
                    self._hat = HatState(x=self._hat.x, y=int(event.value))
                else:
                    unknown_logged = getattr(self, "_unknown_abs_debug_logged", set())
                    if int(event.code) not in unknown_logged:
                        LOGGER.debug("Unknown evdev ABS code ignored: code=%s name=%s value=%s", event.code, name, event.value)
                        unknown_logged.add(int(event.code))
                        self._unknown_abs_debug_logged = unknown_logged
            elif event.type == self._ecodes.EV_KEY:
                name = self._key_code_name(event.code)
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

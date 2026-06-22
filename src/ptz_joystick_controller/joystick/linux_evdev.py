from __future__ import annotations

import errno
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from ..models.joystick_input import ButtonEvent, HatState, JoystickSnapshot, RawAxisState
from .device import JoystickInputProvider

LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True)
class EvdevAxisSpec:
    minimum: int
    maximum: int
    center: int
    flat: int = 0


class LinuxEvdevJoystickProvider(JoystickInputProvider):
    """Linux evdev provider for Logitech Extreme 3D Pro.

    Imports evdev lazily so the application can start without the optional package
    and without a joystick attached.
    """

    # Observed Logitech Extreme 3D Pro mapping on Raspberry Pi / Linux evdev:
    #   ABS 0  -> main X axis / pan
    #   ABS 1  -> main Y axis / tilt
    #   ABS 5  -> twist axis / zoom
    #   ABS 16 -> HAT X
    #   ABS 17 -> HAT Y
    #
    # Keep name-based fallbacks too, but numeric codes are preferred because
    # some evdev environments report codes more reliably than names.
    NUMERIC_AXIS_CODES = {
        0: "pan",
        1: "tilt",
        5: "zoom",
        6: "throttle",
    }
    NUMERIC_HAT_X_CODE = 16
    NUMERIC_HAT_Y_CODE = 17

    # Conservative Logitech Extreme 3D Pro fallbacks observed on Raspberry Pi.
    # evdev absinfo is preferred when available, but these defaults make unit
    # tests and minimal fake devices behave like the real hardware.
    DEFAULT_AXIS_SPECS = {
        0: EvdevAxisSpec(minimum=0, maximum=1023, center=511, flat=4),
        1: EvdevAxisSpec(minimum=0, maximum=1023, center=510, flat=4),
        5: EvdevAxisSpec(minimum=0, maximum=255, center=127, flat=2),
        6: EvdevAxisSpec(minimum=0, maximum=255, center=0, flat=0),
    }

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
        self._unknown_abs_codes_logged: set[int] = set()
        self._axis_specs = self._load_axis_specs()
        self._initialize_state()

    def _initialize_state(self) -> None:
        try:
            abs_state = self._device.active_keys(verbose=False)
            for key_code in abs_state:
                button = self._button_for_code(key_code)
                if button:
                    self._pressed_buttons.add(button)
        except OSError as exc:
            LOGGER.debug("Could not initialize evdev button state: %s", exc)

    def _normalize_code_aliases(self, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (tuple, list, set, frozenset)):
            return tuple(str(item) for item in value if item is not None)
        return (str(value),)

    def _code_names(self, event_type: int, code: int) -> tuple[str, ...]:
        names = self._normalize_code_aliases(self._ecodes.bytype.get(event_type, {}).get(code))
        if names:
            return names
        return (str(code),)

    def _code_name(self, code: int) -> str:
        key_names = self._code_names(self._ecodes.EV_KEY, code)
        if key_names != (str(code),):
            return key_names[0]
        return self._code_names(self._ecodes.EV_ABS, code)[0]

    def _button_for_code(self, code: int) -> str | None:
        # Prefer explicit numeric mapping for real Logitech Extreme 3D Pro
        # hardware where evdev may report multiple aliases for the same code,
        # e.g. code 288 as ("BTN_JOYSTICK", "BTN_TRIGGER").
        numeric_buttons = {
            288: "trigger",
        }
        if int(code) in numeric_buttons:
            return numeric_buttons[int(code)]
        for name in self._code_names(self._ecodes.EV_KEY, int(code)):
            button = self.BUTTON_CODES.get(name)
            if button is not None:
                return button
        return None

    def _load_axis_specs(self) -> dict[int, EvdevAxisSpec]:
        specs = dict(self.DEFAULT_AXIS_SPECS)
        for code in self.NUMERIC_AXIS_CODES:
            try:
                absinfo = self._device.absinfo(code)
            except Exception:
                continue
            minimum = int(getattr(absinfo, "min"))
            maximum = int(getattr(absinfo, "max"))
            flat = int(getattr(absinfo, "flat", 0) or 0)
            fallback = specs.get(code)
            # Most centered joystick axes use the midpoint. Keep a measured
            # fallback center when the midpoint would make the real idle value
            # sit just outside zero and no evdev flat zone is provided.
            center = (minimum + maximum) // 2
            if fallback is not None and flat <= 0 and abs(fallback.center - center) <= 2:
                center = fallback.center
            specs[code] = EvdevAxisSpec(minimum=minimum, maximum=maximum, center=center, flat=max(flat, fallback.flat if fallback else 0))
        return specs

    def _normalize_axis_value(self, code: int, value: int) -> int:
        spec = self._axis_specs.get(code, self.DEFAULT_AXIS_SPECS.get(code))
        if spec is None:
            return int(value)
        if abs(value - spec.center) <= spec.flat:
            return 0
        if value < spec.center:
            span = max(1, spec.center - spec.minimum)
            normalized = (value - spec.center) / span
        else:
            span = max(1, spec.maximum - spec.center)
            normalized = (value - spec.center) / span
        normalized = max(-1.0, min(1.0, normalized))
        if normalized < 0:
            return int(round(normalized * 32768))
        return int(round(normalized * 32767))

    def _set_axis(self, field_name: str, value: int, *, code: int | None = None) -> None:
        internal_value = self._normalize_axis_value(code, value) if code is not None else int(value)
        self._axes = RawAxisState(
            pan=internal_value if field_name == "pan" else self._axes.pan,
            tilt=internal_value if field_name == "tilt" else self._axes.tilt,
            zoom=internal_value if field_name == "zoom" else self._axes.zoom,
            throttle=internal_value if field_name == "throttle" else self._axes.throttle,
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
                numeric_code = int(event.code)
                value = int(event.value)
                if numeric_code in self.NUMERIC_AXIS_CODES:
                    self._set_axis(self.NUMERIC_AXIS_CODES[numeric_code], value, code=numeric_code)
                elif numeric_code == self.NUMERIC_HAT_X_CODE:
                    self._hat = HatState(x=value, y=self._hat.y)
                elif numeric_code == self.NUMERIC_HAT_Y_CODE:
                    self._hat = HatState(x=self._hat.x, y=value)
                else:
                    name = self._code_name(numeric_code)
                    if name in self.AXIS_CODES:
                        self._set_axis(self.AXIS_CODES[name], value, code=numeric_code)
                    elif name == self.HAT_X_CODE:
                        self._hat = HatState(x=value, y=self._hat.y)
                    elif name == self.HAT_Y_CODE:
                        self._hat = HatState(x=self._hat.x, y=value)
                    elif numeric_code not in self._unknown_abs_codes_logged:
                        LOGGER.debug("Ignoring unknown evdev EV_ABS code=%s name=%s value=%s", numeric_code, name, value)
                        self._unknown_abs_codes_logged.add(numeric_code)
            elif event.type == self._ecodes.EV_KEY:
                button = self._button_for_code(event.code)
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

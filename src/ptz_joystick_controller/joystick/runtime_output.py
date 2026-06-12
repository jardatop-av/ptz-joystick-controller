from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from ..models.joystick_input import HatDirection, JoystickSnapshot, RawAxisState
from ..models.joystick_runtime import JoystickHealth
from .runtime import JoystickRuntimeMonitor
from .button_metadata import ButtonMetadataRegistry


@dataclass(frozen=True)
class RuntimeLogMessage:
    message: str
    args: tuple[object, ...] = ()


@dataclass
class JoystickRuntimeOutputFilter:
    """Stateful filter for concise human-readable joystick runtime logs.

    The filter intentionally lives outside the joystick abstraction and mapping
    layers. It only decides what the manual monitor should print.
    """

    axis_log_interval_seconds: float = 0.25
    health_log_interval_seconds: float = 5.0
    last_axes: RawAxisState | None = None
    last_buttons: frozenset[str] = field(default_factory=frozenset)
    last_hat_direction: HatDirection | None = None
    last_axis_log_at: float = field(default=-1_000_000.0)
    last_health_log_at: float = field(default=-1_000_000.0)
    last_health_text: str | None = None

    def health_messages(self, health: JoystickHealth, *, now: float | None = None, force: bool = False) -> list[RuntimeLogMessage]:
        timestamp = monotonic() if now is None else now
        status = health.status_text()
        should_log = force or status != self.last_health_text or (timestamp - self.last_health_log_at) >= self.health_log_interval_seconds
        if not should_log:
            return []
        self.last_health_text = status
        self.last_health_log_at = timestamp
        return [RuntimeLogMessage("Joystick health status: %s", (status,))]

    def snapshot_messages(
        self,
        monitor: JoystickRuntimeMonitor,
        snapshot: JoystickSnapshot,
        *,
        now: float | None = None,
        verbose: bool = False,
    ) -> list[RuntimeLogMessage]:
        timestamp = monotonic() if now is None else now
        messages: list[RuntimeLogMessage] = []

        pressed = snapshot.pressed_buttons
        newly_pressed = sorted(pressed - self.last_buttons)
        newly_released = sorted(self.last_buttons - pressed)
        metadata = ButtonMetadataRegistry(monitor.config.joystick.button_labels)
        for button_name in newly_pressed:
            messages.append(RuntimeLogMessage("Button pressed: %s (%s)", (metadata.label_for(button_name), button_name)))
        for button_name in newly_released:
            messages.append(RuntimeLogMessage("Button released: %s (%s)", (metadata.label_for(button_name), button_name)))
        self.last_buttons = pressed

        hat_direction = snapshot.hat.direction
        if self.last_hat_direction is None:
            self.last_hat_direction = hat_direction
        elif hat_direction != self.last_hat_direction:
            messages.append(RuntimeLogMessage("Hat direction: %s step=%s", (hat_direction.value, monitor.hat_step(snapshot))))
            self.last_hat_direction = hat_direction

        axes_changed = snapshot.axes != self.last_axes
        axis_log_due = (timestamp - self.last_axis_log_at) >= self.axis_log_interval_seconds
        if axes_changed and axis_log_due:
            messages.append(
                RuntimeLogMessage(
                    "Axes raw=%s normalized=%s velocity=%s",
                    (snapshot.axes, monitor.normalized_axes(snapshot), monitor.ptz_velocity(snapshot)),
                )
            )
            self.last_axes = snapshot.axes
            self.last_axis_log_at = timestamp

        if verbose:
            messages.append(
                RuntimeLogMessage(
                    "Verbose snapshot: axes=%s pressed=%s hat=%s velocity=%s",
                    (snapshot.axes, sorted(snapshot.pressed_buttons), snapshot.hat.direction.value, monitor.ptz_velocity(snapshot)),
                )
            )

        return messages

from __future__ import annotations

from dataclasses import dataclass

from .session import CameraSession


@dataclass
class PtzStopWatchdog:
    timeout_ms: int
    session: CameraSession
    enabled: bool = True
    last_tick_ms: int | None = None
    stopped_by_watchdog: bool = False

    def tick(self, now_ms: int) -> None:
        self.last_tick_ms = now_ms
        self.stopped_by_watchdog = False

    def check(self, now_ms: int) -> bool:
        if not self.enabled or self.last_tick_ms is None:
            return False
        if now_ms - self.last_tick_ms >= self.timeout_ms and self.session.state.moving:
            self.session.stop(reason="watchdog_timeout")
            self.stopped_by_watchdog = True
            return True
        return False

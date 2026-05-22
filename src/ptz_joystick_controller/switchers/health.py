from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models.switcher import SwitcherConnectionState, SwitcherStatus
from .base import AbstractSwitcher


class PollableSwitcher(Protocol):
    def poll(self) -> None: ...


@dataclass(frozen=True)
class HealthCheckResult:
    healthy: bool
    status: SwitcherStatus
    error: str | None = None


@dataclass
class SwitcherHealthMonitor:
    switcher: AbstractSwitcher

    def check(self) -> HealthCheckResult:
        try:
            poll_method = getattr(self.switcher, "poll", None)
            if callable(poll_method):
                poll_method()
            status = self.switcher.get_status()
            return HealthCheckResult(healthy=status.state == SwitcherConnectionState.CONNECTED, status=status)
        except Exception as exc:
            status = self.switcher.get_status()
            return HealthCheckResult(healthy=False, status=status, error=str(exc))


@dataclass(frozen=True)
class ReconnectPolicy:
    enabled: bool = True
    max_attempts: int = 3


@dataclass
class SwitcherReconnectManager:
    switcher: AbstractSwitcher
    policy: ReconnectPolicy = ReconnectPolicy()

    def ensure_connected(self) -> bool:
        if self.switcher.is_connected():
            return True
        if not self.policy.enabled:
            return False
        attempts = max(1, self.policy.max_attempts)
        for _ in range(attempts):
            try:
                self.switcher.reconnect()
                if self.switcher.is_connected():
                    return True
            except Exception:
                continue
        return False

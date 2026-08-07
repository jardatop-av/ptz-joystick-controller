from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
import logging
import secrets
import time
from typing import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
import yaml

LOGGER = logging.getLogger(__name__)
ADMIN_USERNAME = "admin"
MIN_PASSWORD_LENGTH = 8


class AuthError(ValueError):
    pass


class AuthStore:
    def __init__(self, path: str | Path = "config.auth.yaml") -> None:
        self.path = Path(path)
        self._lock = RLock()

    @property
    def configured(self) -> bool:
        return bool(self.password_hash())

    def password_hash(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return None
        value = data.get("admin_password_hash")
        return str(value) if value else None

    def verify(self, password: str) -> bool:
        stored = self.password_hash()
        if not stored:
            return False
        try:
            return _PASSWORD_HASHER.verify(stored, password)
        except (VerifyMismatchError, InvalidHashError, ValueError, TypeError):
            return False

    def set_password(self, password: str) -> None:
        validate_password(password)
        hashed = _PASSWORD_HASHER.hash(password)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        with self._lock:
            temp.write_text(yaml.safe_dump({"admin_password_hash": hashed}, sort_keys=False), encoding="utf-8")
            try:
                temp.chmod(0o600)
            except OSError:
                pass
            temp.replace(self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


@dataclass
class Session:
    token: str
    csrf_token: str
    expires_at: float
    source_ip: str


class SessionManager:
    def __init__(self, lifetime_seconds: float = 24 * 60 * 60) -> None:
        self.lifetime_seconds = lifetime_seconds
        self._sessions: dict[str, Session] = {}
        self._lock = RLock()

    def create(self, source_ip: str = "") -> Session:
        session = Session(
            token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=time.time() + self.lifetime_seconds,
            source_ip=source_ip,
        )
        with self._lock:
            self._sessions[session.token] = session
        return session

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session.expires_at <= time.time():
                self._sessions.pop(token, None)
                return None
            return session

    def invalidate(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def invalidate_all(self) -> None:
        with self._lock:
            self._sessions.clear()


class LoginRateLimiter:
    def __init__(self, max_failures: int = 5, cooldown_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_failures = max_failures
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = RLock()

    def limited(self, source_ip: str) -> bool:
        with self._lock:
            count, blocked_until = self._failures.get(source_ip, (0, 0.0))
            if blocked_until and self.clock() >= blocked_until:
                self._failures.pop(source_ip, None)
                return False
            return count >= self.max_failures and self.clock() < blocked_until

    def failure(self, source_ip: str) -> bool:
        with self._lock:
            count, blocked_until = self._failures.get(source_ip, (0, 0.0))
            now = self.clock()
            if blocked_until and now >= blocked_until:
                count, blocked_until = 0, 0.0
            count += 1
            if count >= self.max_failures:
                blocked_until = now + self.cooldown_seconds
            self._failures[source_ip] = (count, blocked_until)
            return count >= self.max_failures

    def success(self, source_ip: str) -> None:
        with self._lock:
            self._failures.pop(source_ip, None)

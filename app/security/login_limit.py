"""Failed-login rate limiting with short lockouts and generic errors."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass

GENERIC_LOGIN_ERROR = "Email or password is not correct."


@dataclass
class LoginLimitConfig:
    max_attempts: int = 5
    window_seconds: int = 15 * 60
    lockout_seconds: int = 15 * 60
    base_delay_seconds: float = 0.4


class LoginRateLimiter:
    def __init__(self, config: LoginLimitConfig | None = None) -> None:
        self.config = config or LoginLimitConfig()
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._lockouts: dict[str, float] = {}

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._lockouts.clear()

    def _key(self, ip: str, email: str) -> str:
        return f"{(ip or 'unknown').strip()}|{(email or '').strip().lower()}"

    def _prune(self, key: str, now: float) -> None:
        window = self.config.window_seconds
        self._failures[key] = [stamp for stamp in self._failures[key] if now - stamp < window]
        until = self._lockouts.get(key)
        if until is not None and until <= now:
            self._lockouts.pop(key, None)

    def check(self, ip: str, email: str) -> tuple[bool, float]:
        """Return (allowed, delay_seconds). delay is a short backoff even when allowed."""
        key = self._key(ip, email)
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            until = self._lockouts.get(key)
            if until and until > now:
                return False, until - now
            failures = len(self._failures[key])
            delay = min(self.config.base_delay_seconds * (2 ** max(failures - 1, 0)), 8.0)
            return True, delay if failures else 0.0

    def register_failure(self, ip: str, email: str) -> None:
        key = self._key(ip, email)
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            self._failures[key].append(now)
            if len(self._failures[key]) >= self.config.max_attempts:
                self._lockouts[key] = now + self.config.lockout_seconds

    def register_success(self, ip: str, email: str) -> None:
        key = self._key(ip, email)
        with self._lock:
            self._failures.pop(key, None)
            self._lockouts.pop(key, None)


_limiter = LoginRateLimiter()


def login_limiter() -> LoginRateLimiter:
    return _limiter

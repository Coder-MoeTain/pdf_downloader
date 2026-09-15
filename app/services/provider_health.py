"""Per-upstream circuit breaker and health tracking."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class HealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"
    BLOCKED = "BLOCKED"
    DISABLED = "DISABLED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class UpstreamStats:
    name: str
    requests: int = 0
    successes: int = 0
    errors: int = 0
    status_429: int = 0
    status_5xx: int = 0
    timeouts: int = 0
    anti_bot: int = 0
    latency_ms_total: float = 0.0
    consecutive_failures: int = 0
    last_request_at: float = 0.0
    opened_at: float = 0.0
    circuit: CircuitState = CircuitState.CLOSED
    samples: list[float] = field(default_factory=list)

    @property
    def average_latency_ms(self) -> float:
        if not self.samples:
            return 0.0
        return sum(self.samples) / len(self.samples)

    def health(self) -> HealthState:
        if self.circuit == CircuitState.OPEN:
            return HealthState.UNAVAILABLE
        if self.status_429 and self.consecutive_failures:
            return HealthState.RATE_LIMITED
        if self.anti_bot >= 3:
            return HealthState.BLOCKED
        if self.errors and self.successes and self.consecutive_failures:
            return HealthState.DEGRADED
        if self.errors and not self.successes and self.requests:
            return HealthState.DEGRADED
        return HealthState.HEALTHY


class ProviderHealthRegistry:
    def __init__(self, *, failure_threshold: int = 5, cooldown_seconds: float = 60.0) -> None:
        self.failure_threshold = max(2, failure_threshold)
        self.cooldown_seconds = max(5.0, cooldown_seconds)
        self._lock = threading.Lock()
        self._stats: dict[str, UpstreamStats] = {}

    def stats(self, name: str) -> UpstreamStats:
        key = (name or "default").strip() or "default"
        with self._lock:
            row = self._stats.get(key)
            if row is None:
                row = UpstreamStats(name=key)
                self._stats[key] = row
            return row

    def snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            rows = list(self._stats.values())
        return [
            {
                "upstream": row.name,
                "requests": row.requests,
                "successes": row.successes,
                "errors": row.errors,
                "status_429": row.status_429,
                "status_5xx": row.status_5xx,
                "timeouts": row.timeouts,
                "anti_bot": row.anti_bot,
                "average_latency_ms": round(row.average_latency_ms, 1),
                "consecutive_failures": row.consecutive_failures,
                "circuit": row.circuit.value,
                "health": row.health().value,
            }
            for row in sorted(rows, key=lambda item: item.name)
        ]

    def allow(self, name: str) -> bool:
        row = self.stats(name)
        now = time.monotonic()
        with self._lock:
            if row.circuit == CircuitState.OPEN:
                if now - row.opened_at >= self.cooldown_seconds:
                    row.circuit = CircuitState.HALF_OPEN
                    return True
                return False
            return True

    def record_success(self, name: str, *, latency_ms: float = 0.0) -> None:
        row = self.stats(name)
        with self._lock:
            row.requests += 1
            row.successes += 1
            row.consecutive_failures = 0
            row.last_request_at = time.monotonic()
            row.circuit = CircuitState.CLOSED
            if latency_ms:
                row.samples.append(latency_ms)
                row.samples = row.samples[-50:]
                row.latency_ms_total += latency_ms

    def record_failure(
        self,
        name: str,
        *,
        status_code: int | None = None,
        timeout: bool = False,
        anti_bot: bool = False,
        latency_ms: float = 0.0,
    ) -> None:
        row = self.stats(name)
        with self._lock:
            row.requests += 1
            row.errors += 1
            row.consecutive_failures += 1
            row.last_request_at = time.monotonic()
            if timeout:
                row.timeouts += 1
            if anti_bot:
                row.anti_bot += 1
            if status_code == 429:
                row.status_429 += 1
            if status_code is not None and status_code >= 500:
                row.status_5xx += 1
            if latency_ms:
                row.samples.append(latency_ms)
                row.samples = row.samples[-50:]
            if row.circuit == CircuitState.HALF_OPEN or row.consecutive_failures >= self.failure_threshold:
                row.circuit = CircuitState.OPEN
                row.opened_at = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._stats.clear()


_REGISTRY = ProviderHealthRegistry()


def provider_health() -> ProviderHealthRegistry:
    return _REGISTRY

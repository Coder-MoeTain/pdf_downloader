"""Async rate limiter with per-provider pacing."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        rps = max(float(requests_per_second), 0.05)
        self.min_interval = 1.0 / rps
        self._lock = asyncio.Lock()
        self._next_time = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_time - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_time = now + self.min_interval

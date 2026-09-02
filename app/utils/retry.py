"""Exponential backoff with jitter for HTTP retries."""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


def backoff_delay(
    retry_count: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.3,
) -> float:
    delay = min(base_delay * (2 ** max(retry_count, 0)), max_delay)
    if jitter:
        delay += random.uniform(0, jitter * delay)
    return delay


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.3,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    import asyncio

    sleeper = sleep or asyncio.sleep
    last_error: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await func()
        except retry_on as exc:
            last_error = exc
            if attempt >= max_attempts - 1:
                break
            await sleeper(backoff_delay(attempt, base_delay, max_delay, jitter))
    assert last_error is not None
    raise last_error

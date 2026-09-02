import asyncio

from app.utils.rate_limit import RateLimiter
from app.utils.retry import backoff_delay, retry_async


def test_backoff_grows_and_caps():
    d0 = backoff_delay(0, base_delay=1.0, max_delay=8.0, jitter=0)
    d3 = backoff_delay(3, base_delay=1.0, max_delay=8.0, jitter=0)
    d10 = backoff_delay(10, base_delay=1.0, max_delay=8.0, jitter=0)
    assert d0 == 1.0
    assert d3 == 8.0
    assert d10 == 8.0


def test_retry_async_succeeds_after_failures():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("fail")
        return "ok"

    result = asyncio.run(retry_async(flaky, max_attempts=4, base_delay=0.001, max_delay=0.01, jitter=0))
    assert result == "ok"
    assert calls["n"] == 3


def test_rate_limiter_spaces_calls():
    limiter = RateLimiter(100)

    async def run():
        await limiter.acquire()
        await limiter.acquire()

    asyncio.run(run())

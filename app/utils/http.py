"""Shared async HTTP client with rate limiting, retries, and 429 handling."""

from __future__ import annotations

import asyncio
import random
import re
from typing import Any

import httpx

from app.config import AppConfig, get_runtime_config
from app.utils.logger import get_logger
from app.utils.rate_limit import RateLimiter
from app.utils.retry import backoff_delay
from app.utils.security import is_safe_url

logger = get_logger("app.http")


class HttpError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AsyncHttpClient:
    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or get_runtime_config()
        env = self.config.env
        timeout = httpx.Timeout(env.request_timeout_seconds, connect=15.0)
        self._client = httpx.AsyncClient(
            headers={"User-Agent": self.config.user_agent_header()},
            timeout=timeout,
            follow_redirects=True,
            max_redirects=env.max_redirects,
        )
        self._sema = asyncio.Semaphore(env.max_concurrent_requests)
        self._limiters: dict[str, RateLimiter] = {}
        self._download_sema = asyncio.Semaphore(env.max_concurrent_downloads)

    def limiter(self, name: str, requests_per_second: float) -> RateLimiter:
        if name not in self._limiters:
            self._limiters[name] = RateLimiter(requests_per_second)
        return self._limiters[name]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncHttpClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        provider: str = "default",
        requests_per_second: float = 5.0,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: float | None = None,
        allow_http: bool = False,
    ) -> httpx.Response:
        if not is_safe_url(url, prefer_https=self.config.prefer_https and not allow_http):
            raise HttpError(f"Blocked unsafe URL: {url}")

        retry_cfg = self.config.retry
        last_error: Exception | None = None
        for attempt in range(retry_cfg.max_attempts):
            await self.limiter(provider, requests_per_second).acquire()
            async with self._sema:
                try:
                    response = await self._client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json,
                        timeout=timeout,
                    )
                except httpx.HTTPError as exc:
                    last_error = exc
                    delay = backoff_delay(attempt, retry_cfg.base_delay, retry_cfg.max_delay, retry_cfg.jitter)
                    logger.warning("%s request failed (%s); retry in %.1fs", provider, exc, delay)
                    await asyncio.sleep(delay)
                    continue

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else backoff_delay(
                        attempt, retry_cfg.base_delay, retry_cfg.max_delay, retry_cfg.jitter
                    )
                except ValueError:
                    wait = backoff_delay(attempt, retry_cfg.base_delay, retry_cfg.max_delay, retry_cfg.jitter)
                wait += random.uniform(0, retry_cfg.jitter)
                logger.warning("%s rate limited (429); sleeping %.1fs", provider, wait)
                await asyncio.sleep(wait)
                last_error = HttpError("HTTP 429", 429)
                continue

            if response.status_code >= 500:
                last_error = HttpError(f"HTTP {response.status_code}", response.status_code)
                await asyncio.sleep(
                    backoff_delay(attempt, retry_cfg.base_delay, retry_cfg.max_delay, retry_cfg.jitter)
                )
                continue

            return response

        raise last_error or HttpError(f"Request to {url} failed")

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        response = await self.request("GET", url, **kwargs)
        if response.status_code >= 400:
            raise HttpError(_http_error_message(response, url), response.status_code)
        return response.json()

    async def get_text(self, url: str, **kwargs: Any) -> str:
        response = await self.request("GET", url, **kwargs)
        if response.status_code >= 400:
            raise HttpError(_http_error_message(response, url), response.status_code)
        return response.text

    async def get_bytes(self, url: str, **kwargs: Any) -> tuple[httpx.Response, bytes]:
        response = await self.request("GET", url, **kwargs)
        return response, response.content


def http_error_detail(response: httpx.Response) -> str:
    """Short, log-safe snippet from an API error body. Never includes query strings or keys."""
    text = (response.text or "").strip()
    if not text:
        return ""
    if text[:1] in "{[":
        try:
            data = response.json()
        except Exception:
            data = None
        if isinstance(data, dict):
            for key in ("error", "message", "detail", "fault", "title"):
                value = data.get(key)
                if value:
                    text = str(value)
                    break
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" \"'")
    return text[:160]


def _http_error_message(response: httpx.Response, url: str) -> str:
    detail = http_error_detail(response)
    message = f"HTTP {response.status_code} for {url}"
    return f"{message} ({detail})" if detail else message

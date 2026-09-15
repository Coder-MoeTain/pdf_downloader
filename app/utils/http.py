"""Shared async HTTP client with rate limiting, retries, and 429 handling."""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import AppConfig, get_runtime_config
from app.exceptions import UnsafeUrlError
from app.security.ssrf import next_redirect_url, resolve_and_validate_host, validate_outbound_url
from app.utils.logger import get_logger
from app.utils.rate_limit import RateLimiter
from app.utils.retry import backoff_delay

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
            follow_redirects=False,
            max_redirects=env.max_redirects,
        )
        self._sema = asyncio.Semaphore(env.max_concurrent_requests)
        self._limiters: dict[str, RateLimiter] = {}
        self._download_sema = asyncio.Semaphore(env.max_concurrent_downloads)

    @property
    def download_sem(self) -> asyncio.Semaphore:
        return self._download_sema

    def limiter(self, name: str, requests_per_second: float) -> RateLimiter:
        if name not in self._limiters:
            self._limiters[name] = RateLimiter(requests_per_second)
        return self._limiters[name]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncHttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def _validate(self, url: str, *, allow_http: bool = False) -> str:
        prefer_https = self.config.prefer_https and not allow_http
        try:
            checked = await asyncio.to_thread(validate_outbound_url, url, prefer_https=prefer_https, resolve_dns=False)
            host = urlparse(checked).hostname or ""
            await asyncio.to_thread(resolve_and_validate_host, host)
            return checked
        except UnsafeUrlError as exc:
            raise HttpError(str(exc) or f"Blocked unsafe URL: {url}") from exc

    async def _redirect_target(self, current_url: str, response: httpx.Response, *, allow_http: bool) -> str:
        location = response.headers.get("location")
        try:
            nxt = next_redirect_url(str(response.url) or current_url, location)
        except UnsafeUrlError as exc:
            raise HttpError(str(exc) or "Blocked redirect") from exc
        return await self._validate(nxt, allow_http=allow_http)

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
        retry_cfg = self.config.retry
        max_hops = max(int(self.config.env.max_redirects), 0)
        last_error: Exception | None = None
        for attempt in range(retry_cfg.max_attempts):
            current = url
            await self.limiter(provider, requests_per_second).acquire()
            try:
                for _hop in range(max_hops + 1):
                    current = await self._validate(current, allow_http=allow_http)
                    async with self._sema:
                        try:
                            response = await self._client.request(
                                method,
                                current,
                                headers=headers,
                                params=params if current == url else None,
                                json=json if current == url else None,
                                timeout=timeout,
                                follow_redirects=False,
                            )
                        except httpx.HTTPError as exc:
                            last_error = exc
                            delay = backoff_delay(attempt, retry_cfg.base_delay, retry_cfg.max_delay, retry_cfg.jitter)
                            logger.warning("%s request failed (%s); retry in %.1fs", provider, exc, delay)
                            await asyncio.sleep(delay)
                            break

                    if response.is_redirect:
                        current = await self._redirect_target(current, response, allow_http=allow_http)
                        params = None
                        json = None
                        continue

                    if response.status_code == 429:
                        from app.services.provider_health import provider_health

                        provider_health().record_failure(provider, status_code=429)
                        retry_after = response.headers.get("Retry-After")
                        try:
                            wait = (
                                float(retry_after)
                                if retry_after
                                else backoff_delay(attempt, retry_cfg.base_delay, retry_cfg.max_delay, retry_cfg.jitter)
                            )
                        except ValueError:
                            wait = backoff_delay(attempt, retry_cfg.base_delay, retry_cfg.max_delay, retry_cfg.jitter)
                        wait += random.uniform(0, retry_cfg.jitter)
                        logger.warning("%s rate limited (429); sleeping %.1fs", provider, wait)
                        await asyncio.sleep(wait)
                        last_error = HttpError("HTTP 429", 429)
                        break

                    if response.status_code >= 500:
                        from app.services.provider_health import provider_health

                        provider_health().record_failure(provider, status_code=response.status_code)
                        last_error = HttpError(f"HTTP {response.status_code}", response.status_code)
                        await asyncio.sleep(
                            backoff_delay(attempt, retry_cfg.base_delay, retry_cfg.max_delay, retry_cfg.jitter)
                        )
                        break

                    if 200 <= response.status_code < 400 and not response.is_redirect:
                        from app.services.provider_health import provider_health

                        provider_health().record_success(provider)
                    return response
                else:
                    last_error = HttpError(f"Too many redirects for {url}")
            except HttpError as exc:
                last_error = exc
                if "Blocked" in str(exc) or "unsafe" in str(exc).lower() or "not allowed" in str(exc).lower():
                    raise

        raise last_error or HttpError(f"Request to {url} failed")

    @asynccontextmanager
    async def safe_stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | float | None = None,
        allow_http: bool = False,
    ) -> AsyncIterator[httpx.Response]:
        current = url
        max_hops = max(int(self.config.env.max_redirects), 0)
        for _hop in range(max_hops + 1):
            current = await self._validate(current, allow_http=allow_http)
            async with self._client.stream(
                method,
                current,
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    current = await self._redirect_target(current, response, allow_http=allow_http)
                    continue
                yield response
                return
        raise HttpError(f"Too many redirects for {url}")

    def stream_download(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | float | None = None,
        allow_http: bool = False,
    ):
        return self.safe_stream("GET", url, headers=headers, timeout=timeout, allow_http=allow_http)

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        response = await self.request("GET", url, **kwargs)
        if response.status_code >= 400:
            raise HttpError(_http_error_message(response, url), response.status_code)
        if not (response.content or b"").strip():
            logger.warning("Empty JSON body from %s; retrying once", url)
            response = await self.request("GET", url, **kwargs)
            if response.status_code >= 400:
                raise HttpError(_http_error_message(response, url), response.status_code)
        return parse_json_response(response, url)

    async def get_text(self, url: str, **kwargs: Any) -> str:
        response = await self.request("GET", url, **kwargs)
        if response.status_code >= 400:
            raise HttpError(_http_error_message(response, url), response.status_code)
        return response.text

    async def get_bytes(self, url: str, **kwargs: Any) -> tuple[httpx.Response, bytes]:
        response = await self.request("GET", url, **kwargs)
        return response, response.content


def parse_json_response(response: httpx.Response, url: str) -> Any:
    """Decode JSON, or raise HttpError with a short body snippet instead of a raw decode traceback."""
    if not (response.content or b"").strip():
        raise HttpError(f"Empty JSON response from {url}", response.status_code)
    try:
        return response.json()
    except ValueError:
        snippet = http_error_detail(response) or "non-JSON body"
        lower = snippet.lower()
        if any(
            token in lower for token in ("not a bot", "captcha", "cf-challenge", "attention required", "just a moment")
        ):
            raise HttpError(
                f"Blocked by anti-bot challenge from {url} ({snippet})",
                response.status_code,
            ) from None
        raise HttpError(f"Invalid JSON from {url} ({snippet})", response.status_code) from None


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

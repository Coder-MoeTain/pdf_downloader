"""Security response headers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


def security_headers(*, production: bool, https: bool) -> dict[str, str]:
    csp = (
        "default-src 'self'; "
        "img-src 'self' data: https:; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "script-src 'self' https://cdn.jsdelivr.net https://static.cloudflareinsights.com; "
        "connect-src 'self' https://cdn.jsdelivr.net https://static.cloudflareinsights.com https://cloudflareinsights.com; "
        "frame-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'"
    )
    headers = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "X-Frame-Options": "SAMEORIGIN",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "X-DNS-Prefetch-Control": "off",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    if production and https:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        from app.config import app_env, https_assumed

        production = app_env() == "production"
        for key, value in security_headers(production=production, https=https_assumed(request)).items():
            response.headers.setdefault(key, value)
        return response

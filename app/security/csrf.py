"""Session CSRF tokens with constant-time comparison."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.exceptions import CsrfError

CSRF_SESSION_KEY = "_csrf_token"
CSRF_COOKIE = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_EXEMPT_PATHS = {
    "/auth/google/callback",
}


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def get_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or len(token) < 16:
        token = generate_csrf_token()
        request.session[CSRF_SESSION_KEY] = token
    return token


def rotate_csrf_token(request: Request) -> str:
    token = generate_csrf_token()
    request.session[CSRF_SESSION_KEY] = token
    return token


async def _form_token(request: Request) -> str:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        value = form.get(CSRF_FORM_FIELD)
        return str(value or "").strip()
    return ""


def tokens_match(expected: str, offered: str) -> bool:
    if not expected or not offered:
        return False
    if len(expected) != len(offered):
        # compare_digest requires equal length; still fail closed
        return hmac.compare_digest(expected, expected) and False
    return hmac.compare_digest(expected, offered)


def csrf_failure(request: Request) -> Response:
    path = request.url.path
    if path.startswith("/api/") or "application/json" in (request.headers.get("accept") or ""):
        return JSONResponse(
            {"ok": False, "error": CsrfError.public_message, "code": "csrf"},
            status_code=403,
        )
    referer = request.headers.get("referer") or "/"
    return RedirectResponse(referer if referer.startswith("/") else "/", status_code=303)


async def csrf_protect(request: Request) -> None:
    """Validate mutating requests. Runs as a FastAPI dependency so form parsing is shared."""
    path = request.url.path
    if path.startswith("/static") or path in _EXEMPT_PATHS:
        return
    if request.method.upper() in SAFE_METHODS:
        return
    if not hasattr(request, "session"):
        raise CsrfError()
    token = get_csrf_token(request)
    offered = (request.headers.get(CSRF_HEADER) or request.headers.get("X-CSRFToken") or "").strip()
    if not offered:
        offered = await _form_token(request)
    if not tokens_match(token, offered):
        raise CsrfError()


class CsrfMiddleware(BaseHTTPMiddleware):
    """Sets the readable CSRF cookie. Validation lives in `csrf_protect` to avoid draining POST bodies."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        path = request.url.path
        if path.startswith("/static") or path in _EXEMPT_PATHS:
            return await call_next(request)
        if not hasattr(request, "session"):
            return await call_next(request)

        token = get_csrf_token(request)
        response = await call_next(request)
        token = str(request.session.get(CSRF_SESSION_KEY) or token)
        response.set_cookie(
            CSRF_COOKIE,
            token,
            httponly=False,
            samesite="lax",
            secure=_cookie_secure(request),
            path="/",
            max_age=60 * 60 * 12,
        )
        return response


def _cookie_secure(request: Request) -> bool:
    from app.config import cookie_secure

    return cookie_secure(request)

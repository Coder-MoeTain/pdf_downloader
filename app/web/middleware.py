"""Auth gate, trusted hosts, and related request middleware."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.auth import (
    ROLE_ADMIN,
    current_user,
    is_admin_path,
    is_public_path,
    setup_required,
    user_role,
)
from app.web.flash import set_flash


class AuthGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        path = request.url.path
        if is_public_path(path):
            return await call_next(request)

        needs_setup = await asyncio.to_thread(setup_required)
        if needs_setup:
            if path.startswith("/api/"):
                return JSONResponse(
                    {"ok": False, "error": "setup_required", "message": "Create the first administrator at /setup."},
                    status_code=401,
                )
            nxt = path
            if request.url.query:
                nxt = f"{path}?{request.url.query}"
            return RedirectResponse(f"/setup?next={nxt}", status_code=302)

        user = current_user(request)
        if user is None:
            if path.startswith("/api/"):
                return JSONResponse({"ok": False, "error": "Sign in required"}, status_code=401)
            nxt = path
            if request.url.query:
                nxt = f"{path}?{request.url.query}"
            return RedirectResponse(f"/login?next={nxt}", status_code=302)

        if is_admin_path(path) and user_role(user) != ROLE_ADMIN:
            if path.startswith("/api/"):
                return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)
            set_flash(request, "Sources, Crawler, System, and Settings are limited to admin accounts.", "warning")
            return RedirectResponse("/", status_code=302)

        from app.services.usage import touch_presence

        touch_presence(user, path=path)
        return await call_next(request)

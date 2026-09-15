"""First-run setup, login, OAuth, logout, and account routes."""

from __future__ import annotations

import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import OperationalError

from app.auth import (
    PASSWORD_MIN_LENGTH,
    ROLE_ADMIN,
    ROLE_USER,
    authenticate_local,
    create_local_user,
    current_user,
    get_oauth,
    google_login_enabled,
    hash_password,
    is_admin_path,
    rotate_session,
    safe_next_path,
    set_user_role,
    setup_required,
    upsert_google_user,
    user_role,
    user_to_session,
    verify_password,
)
from app.database.connection import retry_on_sqlite_lock, session_scope
from app.database.models import User
from app.services.usage import drop_presence, record_usage, touch_presence
from app.web.dependencies import (
    _account_users,
    _ctx,
    _login_ctx,
    templates,
)
from app.web.flash import set_flash

router = APIRouter()


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, next: str = "/"):
    if not setup_required():
        return RedirectResponse("/login", status_code=302)
    from app.security.bootstrap import ensure_bootstrap_token

    ensure_bootstrap_token()
    return templates.TemplateResponse(request, "setup.html", _login_ctx(request, next))


@router.post("/setup")
def setup_submit(
    request: Request,
    bootstrap_token: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    name: str = Form(""),
    next: str = Form("/"),
):
    from app.security.audit import record_audit
    from app.security.bootstrap import consume_bootstrap_token, ensure_bootstrap_token, verify_bootstrap_token
    from app.security.passwords import PasswordPolicyError

    if not setup_required():
        return RedirectResponse("/login", status_code=302)
    ensure_bootstrap_token()
    nxt = safe_next_path(next)
    if not verify_bootstrap_token(bootstrap_token):
        record_audit("setup_failed", request=request, result="denied", metadata={"reason": "token"})
        set_flash(request, "That setup token is not valid.", "danger")
        return templates.TemplateResponse(request, "setup.html", _login_ctx(request, nxt), status_code=403)
    try:

        def _create():
            with session_scope() as session:
                row = create_local_user(session, email=email, password=password, name=name, role=ROLE_ADMIN)
                consume_bootstrap_token()
                return user_to_session(row)

        payload = retry_on_sqlite_lock(_create)
    except ValueError as exc:
        set_flash(request, str(exc), "danger")
        return templates.TemplateResponse(request, "setup.html", _login_ctx(request, nxt), status_code=400)
    except PasswordPolicyError as exc:
        set_flash(request, str(exc), "danger")
        return templates.TemplateResponse(request, "setup.html", _login_ctx(request, nxt), status_code=400)
    except OperationalError:
        set_flash(request, "The library database was busy. Wait a moment and try again.", "warning")
        return templates.TemplateResponse(request, "setup.html", _login_ctx(request, nxt), status_code=503)
    rotate_session(request, payload)
    touch_presence(payload, path="/")
    record_usage(request, "login", f"Created administrator {payload['email']}")
    record_audit(
        "user_created",
        request=request,
        actor_user_id=payload.get("id"),
        target_type="user",
        target_id=str(payload.get("id") or ""),
        metadata={"role": "admin", "setup": True},
    )
    set_flash(request, f"Administrator {payload['email']} created. Keep this password safe.", "success")
    return RedirectResponse(nxt if nxt != "/setup" else "/", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if setup_required():
        return RedirectResponse("/setup", status_code=302)
    if current_user(request):
        return RedirectResponse(safe_next_path(next), status_code=302)
    return templates.TemplateResponse(request, "login.html", _login_ctx(request, next))


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    name: str = Form(""),
    next: str = Form("/"),
):
    from app.security.audit import client_ip, record_audit
    from app.security.login_limit import GENERIC_LOGIN_ERROR, login_limiter

    if setup_required():
        return RedirectResponse("/setup", status_code=302)
    nxt = safe_next_path(next)
    email = (email or "").strip()
    ip = client_ip(request)
    allowed, delay = login_limiter().check(ip, email)
    if not allowed:
        record_audit("login_failure", request=request, result="locked", metadata={"email_present": bool(email)})
        set_flash(request, GENERIC_LOGIN_ERROR, "danger")
        return templates.TemplateResponse(request, "login.html", _login_ctx(request, nxt), status_code=429)
    if delay:
        time.sleep(min(delay, 2.0))
    if not email or not password:
        set_flash(request, "Enter your email and password.", "warning")
        return templates.TemplateResponse(request, "login.html", _login_ctx(request, nxt), status_code=400)
    try:

        def _persist_local():
            with session_scope() as session:
                row = authenticate_local(session, email, password)
                if row is None:
                    return None
                return user_to_session(row)

        payload = retry_on_sqlite_lock(_persist_local)
        if payload is None:
            login_limiter().register_failure(ip, email)
            record_audit("login_failure", request=request, result="denied", metadata={"email_present": True})
            set_flash(request, GENERIC_LOGIN_ERROR, "danger")
            return templates.TemplateResponse(request, "login.html", _login_ctx(request, nxt), status_code=401)
        login_limiter().register_success(ip, email)
        rotate_session(request, payload)
        signed_email = payload["email"]
        is_admin = user_role(payload) == ROLE_ADMIN
        touch_presence(payload, path="/")
        record_usage(request, "login", f"Signed in as {signed_email}")
        record_audit("login_success", request=request, actor_user_id=payload.get("id"), result="success")
    except ValueError as exc:
        set_flash(request, str(exc), "danger")
        return templates.TemplateResponse(request, "login.html", _login_ctx(request, nxt), status_code=400)
    except OperationalError:
        set_flash(request, "The library database was busy. Wait a moment and try again.", "warning")
        return templates.TemplateResponse(request, "login.html", _login_ctx(request, nxt), status_code=503)
    if is_admin_path(nxt) and not is_admin:
        nxt = "/"
    set_flash(request, f"Signed in as {signed_email}.", "success")
    return RedirectResponse(nxt, status_code=303)


@router.get("/auth/google")
async def auth_google(request: Request, next: str = "/"):
    if setup_required():
        set_flash(request, "Create the first administrator at /setup before using Google sign-in.", "warning")
        return RedirectResponse("/setup", status_code=302)
    if not google_login_enabled():
        set_flash(
            request,
            "Google sign-in is not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env.",
            "warning",
        )
        return RedirectResponse("/login", status_code=302)
    request.session["oauth_next"] = safe_next_path(next)
    redirect_uri = str(request.url_for("auth_google_callback"))
    return await get_oauth().google.authorize_redirect(request, redirect_uri)


@router.get("/auth/google/callback", name="auth_google_callback")
async def auth_google_callback(request: Request):
    from app.security.audit import record_audit

    if setup_required():
        set_flash(request, "Create the first administrator at /setup before using Google sign-in.", "warning")
        return RedirectResponse("/setup", status_code=302)
    nxt = safe_next_path(request.session.pop("oauth_next", "/"))
    try:
        token = await get_oauth().google.authorize_access_token(request)
    except Exception:
        set_flash(request, "Google sign-in failed. Try again.", "danger")
        return RedirectResponse("/login", status_code=302)
    info = token.get("userinfo") or {}
    email = str(info.get("email") or "").strip().lower()
    google_id = str(info.get("sub") or "").strip()
    if not email or not google_id:
        set_flash(request, "Google did not return an email address for this account.", "danger")
        return RedirectResponse("/login", status_code=302)
    try:

        def _persist_google():
            with session_scope() as session:
                row = upsert_google_user(
                    session,
                    google_id=google_id,
                    email=email,
                    name=str(info.get("name") or ""),
                    picture=str(info.get("picture") or "") or None,
                )
                return user_to_session(row), bool(row.is_admin)

        payload, is_admin = retry_on_sqlite_lock(_persist_google)
        rotate_session(request, payload)
        touch_presence(payload, path="/")
        record_usage(request, "login", f"Signed in with Google as {email}")
        record_audit(
            "login_success",
            request=request,
            actor_user_id=payload.get("id"),
            result="success",
            metadata={"provider": "google"},
        )
    except OperationalError:
        set_flash(request, "The library database was busy. Wait a moment and sign in again.", "warning")
        return RedirectResponse("/login", status_code=302)
    if is_admin_path(nxt) and not is_admin:
        nxt = "/"
    set_flash(request, f"Signed in as {email}.", "success")
    return RedirectResponse(nxt, status_code=302)


@router.post("/logout")
def logout(request: Request):
    from app.security.audit import record_audit

    user = current_user(request)
    if user:
        record_usage(request, "logout", f"Signed out {user.get('email') or user.get('name') or ''}".strip())
        record_audit("logout", request=request, actor_user_id=user.get("id"), result="success")
        drop_presence(user.get("id"))
    request.session.clear()
    response = RedirectResponse("/login", status_code=303)
    return response


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login?next=/account", status_code=302)
    with session_scope() as session:
        members = _account_users(session) if user_role(user) == ROLE_ADMIN else []
    return templates.TemplateResponse(
        request,
        "account.html",
        _ctx(request, members=members, password_min=PASSWORD_MIN_LENGTH, roles=(ROLE_USER, ROLE_ADMIN)),
    )


@router.post("/account/profile")
def account_save_profile(request: Request, name: str = Form("")):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login?next=/account", status_code=302)
    with session_scope() as session:
        row = session.get(User, user["id"])
        if row is None:
            request.session.clear()
            return RedirectResponse("/login", status_code=302)
        row.name = (name or "").strip() or row.email.split("@")[0]
        session.flush()
        request.session["user"] = user_to_session(row)
    set_flash(request, "Profile saved.", "success")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/password")
def account_save_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login?next=/account", status_code=302)
    if new_password != confirm_password:
        set_flash(request, "New password and confirmation do not match.", "danger")
        return RedirectResponse("/account", status_code=303)
    if len(new_password) < PASSWORD_MIN_LENGTH:
        set_flash(request, f"Password must be at least {PASSWORD_MIN_LENGTH} characters.", "danger")
        return RedirectResponse("/account", status_code=303)
    with session_scope() as session:
        row = session.get(User, user["id"])
        if row is None:
            request.session.clear()
            return RedirectResponse("/login", status_code=302)
        if row.password_hash and not verify_password(current_password, row.password_hash):
            set_flash(request, "Current password is not correct.", "danger")
            return RedirectResponse("/account", status_code=303)
        row.password_hash = hash_password(new_password)
        session.flush()
        request.session["user"] = user_to_session(row)
    set_flash(request, "Password updated.", "success")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/users")
def account_add_user(
    request: Request,
    email: str = Form(""),
    name: str = Form(""),
    password: str = Form(""),
    role: str = Form(ROLE_USER),
):
    user = current_user(request)
    if user is None or user_role(user) != ROLE_ADMIN:
        return RedirectResponse("/", status_code=302)
    try:
        with session_scope() as session:
            create_local_user(session, email=email, password=password, name=name, role=role)
    except ValueError as exc:
        set_flash(request, str(exc), "danger")
        return RedirectResponse("/account", status_code=303)
    set_flash(request, f"Added {email.strip().lower()} as {role}.", "success")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/users/{user_id}/role")
def account_set_role(request: Request, user_id: int, role: str = Form(ROLE_USER)):
    user = current_user(request)
    if user is None or user_role(user) != ROLE_ADMIN:
        return RedirectResponse("/", status_code=302)
    try:
        with session_scope() as session:
            row = set_user_role(session, user_id, role)
            if row.id == user.get("id"):
                request.session["user"] = user_to_session(row)
    except ValueError as exc:
        set_flash(request, str(exc), "danger")
        return RedirectResponse("/account", status_code=303)
    set_flash(request, "Role updated.", "success")
    return RedirectResponse("/account", status_code=303)

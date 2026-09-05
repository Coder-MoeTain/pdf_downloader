from fastapi.testclient import TestClient

from app.auth import upsert_google_user
from app.config import load_config
from app.database.connection import session_scope
from app.database.models import User
from app.web import app
from sqlalchemy import func, select


def _enable_google(monkeypatch, user=None):
    monkeypatch.setattr("app.web.google_login_enabled", lambda: True)
    monkeypatch.setattr("app.auth.google_login_enabled", lambda: True)
    monkeypatch.setattr("app.web.auth_required", lambda: True)
    monkeypatch.setattr("app.auth.auth_required", lambda: True)
    monkeypatch.setattr("app.web.current_user", lambda _request: user)
    monkeypatch.setattr("app.auth.current_user", lambda _request: user)
    monkeypatch.setattr(
        "app.web.user_is_admin",
        lambda _request: bool(user and (user.get("is_admin") or user.get("role") == "admin")),
    )
    monkeypatch.setattr(
        "app.auth.user_is_admin",
        lambda _request: bool(user and (user.get("is_admin") or user.get("role") == "admin")),
    )
    monkeypatch.setattr(
        "app.web.user_role",
        lambda value: "admin" if value and (value.get("is_admin") or value.get("role") == "admin") else "user",
    )


def test_settings_open_when_nobody_has_signed_up(tmp_db):
    client = TestClient(app)
    response = client.get("/settings?section=workspace")
    assert response.status_code == 200
    assert "Show paywalled papers" in response.text
    assert 'href="/sources"' in response.text
    assert "Settings" in response.text
    assert "Log in" in response.text
    login = client.get("/login")
    assert login.status_code == 200
    assert "Create the admin account" in login.text
    assert "Continue with Gmail" in login.text
    assert "Log out" not in login.text


def test_unauthenticated_user_is_sent_to_login(tmp_db, monkeypatch):
    _enable_google(monkeypatch, user=None)
    client = TestClient(app, follow_redirects=False)
    home = client.get("/")
    assert home.status_code == 302
    assert "/login" in home.headers["location"]
    settings = client.get("/settings")
    assert settings.status_code == 302
    assert "/login" in settings.headers["location"]
    login = client.get("/login")
    assert login.status_code == 200
    assert "Continue with Gmail" in login.text
    assert "/static/theme.css" in login.text
    assert 'data-theme-toggle' in login.text
    assert 'data-bs-theme' in login.text


def test_non_admin_cannot_open_sources_or_settings(tmp_db, monkeypatch):
    user = {
        "id": 1,
        "email": "reader@gmail.com",
        "name": "Reader",
        "picture": "",
        "role": "user",
        "is_admin": False,
        "has_password": True,
    }
    _enable_google(monkeypatch, user=user)
    client = TestClient(app, follow_redirects=False)
    settings = client.get("/settings")
    assert settings.status_code == 302
    assert settings.headers["location"].endswith("/")
    assert "settings" not in settings.headers["location"].lower()
    sources = client.get("/sources")
    assert sources.status_code == 302
    assert sources.headers["location"].endswith("/")
    crawler = client.get("/crawler")
    assert crawler.status_code == 302
    assert crawler.headers["location"].endswith("/")
    api = client.get("/api/sources/1")
    assert api.status_code == 403
    home = client.get("/", follow_redirects=True)
    assert home.status_code == 200
    assert 'href="/sources"' not in home.text
    assert 'href="/settings"' not in home.text
    assert "Log out" in home.text
    assert "Reader" in home.text


def test_admin_can_open_sources_and_settings(tmp_db, monkeypatch):
    user = {
        "id": 1,
        "email": "admin@gmail.com",
        "name": "Admin",
        "picture": "",
        "role": "admin",
        "is_admin": True,
        "has_password": True,
    }
    _enable_google(monkeypatch, user=user)
    client = TestClient(app)
    settings = client.get("/settings?section=workspace")
    assert settings.status_code == 200
    assert "Show paywalled papers" in settings.text
    sources = client.get("/sources")
    assert sources.status_code == 200
    home = client.get("/")
    assert 'href="/sources"' in home.text
    assert 'href="/settings"' in home.text
    assert "Log out" in home.text


def test_first_google_user_becomes_admin_when_list_is_empty(tmp_db, monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "you@example.com")
    monkeypatch.setenv("UNPAYWALL_EMAIL", "")
    monkeypatch.setenv("GOOGLE_ADMIN_EMAILS", "")
    load_config.cache_clear()
    with session_scope() as session:
        row = upsert_google_user(
            session,
            google_id="sub-1",
            email="owner@gmail.com",
            name="Owner",
        )
        assert row.is_admin is True
        assert row.role == "admin"
        second = upsert_google_user(
            session,
            google_id="sub-2",
            email="guest@gmail.com",
            name="Guest",
        )
        assert second.is_admin is False
        assert second.role == "user"
        assert session.scalar(select(func.count(User.id))) == 2


def test_local_admin_login_logout_and_user_settings(tmp_db):
    client = TestClient(app)
    created = client.post(
        "/login",
        data={"email": "admin@lab.test", "password": "secret123", "name": "Lab Admin", "next": "/"},
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert "Lab Admin" in created.text
    assert "Log out" in created.text
    account = client.get("/account")
    assert account.status_code == 200
    assert "User settings" in account.text
    assert "Lab Admin" in account.text
    saved = client.post("/account/profile", data={"name": "Collector Admin"}, follow_redirects=True)
    assert saved.status_code == 200
    assert "Collector Admin" in saved.text
    added = client.post(
        "/account/users",
        data={"email": "reader@lab.test", "name": "Reader", "password": "secret123", "role": "user"},
        follow_redirects=True,
    )
    assert added.status_code == 200
    assert "reader@lab.test" in added.text
    client.post("/logout")
    denied = client.get("/settings", follow_redirects=False)
    assert denied.status_code == 302
    assert "/login" in denied.headers["location"]
    as_user = client.post(
        "/login",
        data={"email": "reader@lab.test", "password": "secret123", "next": "/"},
        follow_redirects=False,
    )
    assert as_user.status_code == 303
    home = client.get("/")
    assert "Reader" in home.text
    assert "Log out" in home.text
    assert 'href="/settings"' not in home.text
    assert 'href="/sources"' not in home.text
    assert client.get("/settings", follow_redirects=False).status_code == 302
    assert client.get("/account").status_code == 200


def test_seed_admin_creates_default_account(tmp_db):
    from app.auth import DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD, authenticate_local, seed_admin_account

    result = seed_admin_account()
    assert result["status"] == "created"
    assert result["email"] == DEFAULT_ADMIN_EMAIL
    with session_scope() as session:
        row = authenticate_local(session, DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD)
        assert row is not None
        assert row.role == "admin"
        assert row.is_admin is True
    again = seed_admin_account()
    assert again["status"] == "exists"
    with session_scope() as session:
        assert session.scalar(select(func.count(User.id))) == 1


def test_seed_admin_reset_password(tmp_db):
    from app.auth import authenticate_local, seed_admin_account

    seed_admin_account(email="ops@lab.test", password="secret123", name="Ops")
    updated = seed_admin_account(
        email="ops@lab.test",
        password="newpass123",
        name="Ops Admin",
        reset_password=True,
    )
    assert updated["status"] == "updated"
    with session_scope() as session:
        assert authenticate_local(session, "ops@lab.test", "secret123") is None
        row = authenticate_local(session, "ops@lab.test", "newpass123")
        assert row is not None
        assert row.name == "Ops Admin"
        assert row.role == "admin"

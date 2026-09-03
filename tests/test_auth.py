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
    monkeypatch.setattr("app.web.current_user", lambda _request: user)
    monkeypatch.setattr("app.auth.current_user", lambda _request: user)
    monkeypatch.setattr(
        "app.web.user_is_admin",
        lambda _request: bool(user and user.get("is_admin")),
    )
    monkeypatch.setattr(
        "app.auth.user_is_admin",
        lambda _request: bool(user and user.get("is_admin")),
    )


def test_settings_open_when_google_login_is_off(tmp_db):
    client = TestClient(app)
    response = client.get("/settings?section=workspace")
    assert response.status_code == 200
    assert "Show paywalled papers" in response.text
    assert 'href="/sources"' in response.text
    assert "Settings" in response.text


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
    assert "Continue with Google" in login.text


def test_non_admin_cannot_open_sources_or_settings(tmp_db, monkeypatch):
    user = {
        "id": 1,
        "email": "reader@gmail.com",
        "name": "Reader",
        "picture": "",
        "is_admin": False,
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
    assert "sources" not in sources.headers["location"].lower()
    api = client.get("/api/sources/1")
    assert api.status_code == 403
    home = client.get("/", follow_redirects=True)
    assert home.status_code == 200
    assert 'href="/sources"' not in home.text
    assert 'href="/settings"' not in home.text
    assert "Sign out" in home.text
    assert "Reader" in home.text


def test_admin_can_open_sources_and_settings(tmp_db, monkeypatch):
    user = {
        "id": 1,
        "email": "admin@gmail.com",
        "name": "Admin",
        "picture": "",
        "is_admin": True,
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
        second = upsert_google_user(
            session,
            google_id="sub-2",
            email="guest@gmail.com",
            name="Guest",
        )
        assert second.is_admin is False
        assert session.scalar(select(func.count(User.id))) == 2

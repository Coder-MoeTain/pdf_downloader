import pytest
from fastapi.testclient import TestClient

from app.auth import upsert_google_user
from app.config import load_config
from app.database.connection import session_scope
from app.web import app
from tests.conftest import patch_web


def _enable_google(monkeypatch, user=None):
    patch_web(monkeypatch, "google_login_enabled", lambda: True)
    patch_web(monkeypatch, "auth_required", lambda: True)
    patch_web(monkeypatch, "current_user", lambda _request: user)
    patch_web(
        monkeypatch,
        "user_is_admin",
        lambda _request: bool(user and (user.get("is_admin") or user.get("role") == "admin")),
    )
    patch_web(
        monkeypatch,
        "user_role",
        lambda value: "admin" if value and (value.get("is_admin") or value.get("role") == "admin") else "user",
    )


@pytest.mark.setup_mode
def test_anonymous_home_redirects_to_setup(tmp_db):
    client = TestClient(app, follow_redirects=False)
    home = client.get("/")
    assert home.status_code == 302
    assert "/setup" in home.headers["location"]
    assert "admin" not in home.text.lower() or "Create the administrator" not in home.text


@pytest.mark.setup_mode
def test_anonymous_settings_denied(tmp_db):
    client = TestClient(app, follow_redirects=False)
    settings = client.get("/settings")
    assert settings.status_code == 302
    assert "/setup" in settings.headers["location"]
    api = client.get("/api/sources/1")
    assert api.status_code == 401
    assert api.json()["error"] == "setup_required"


@pytest.mark.setup_mode
def test_anonymous_post_privileged_denied(tmp_db):
    client = TestClient(app, follow_redirects=False)
    client.get("/setup")
    response = client.post("/settings/workspace", data={"timezone": "UTC"})
    assert response.status_code in {302, 401, 403}


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
    assert "data-theme-toggle" in login.text
    assert "data-bs-theme" in login.text
    assert "Create the admin account" not in login.text


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


def test_google_user_does_not_become_admin_automatically(tmp_db, monkeypatch):
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
        assert row.is_admin is False
        assert row.role == "user"
        second = upsert_google_user(
            session,
            google_id="sub-2",
            email="guest@gmail.com",
            name="Guest",
        )
        assert second.is_admin is False
        assert second.role == "user"


def test_local_admin_login_logout_and_user_settings(auth_client):
    client = auth_client
    home = client.get("/")
    assert home.status_code == 200
    assert "Pytest Admin" in home.text
    assert "Log out" in home.text
    account = client.get("/account")
    assert account.status_code == 200
    assert "User settings" in account.text
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


def test_library_and_downloads_keep_login_session(auth_client):
    client = auth_client
    for path in ("/library", "/downloads", "/reports"):
        page = client.get(path)
        assert page.status_code == 200
        assert "Pytest Admin" in page.text
        assert "Log out" in page.text
        assert ">Log in<" not in page.text


def test_seed_admin_requires_explicit_credentials(tmp_db):
    from app.auth import seed_admin_account

    with pytest.raises(ValueError):
        seed_admin_account()
    result = seed_admin_account(email="ops@lab.test", password="secret123", name="Ops")
    assert result["status"] == "created"
    assert result["email"] == "ops@lab.test"


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


def test_unauthenticated_admin_endpoint_denied(tmp_db):
    client = TestClient(app, follow_redirects=False)
    assert client.get("/settings").status_code == 302
    assert client.post("/settings/workspace", data={"timezone": "UTC"}).status_code in {302, 401, 403}


def test_get_logout_does_not_logout(auth_client):
    client = auth_client
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 405
    home = client.get("/")
    assert "Log out" in home.text

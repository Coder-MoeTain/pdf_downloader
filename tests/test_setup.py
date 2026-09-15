import pytest
from fastapi.testclient import TestClient

from app.auth import authenticate_local, user_count
from app.database.connection import session_scope
from app.security.bootstrap import ensure_bootstrap_token, peek_bootstrap_token
from app.web import app


@pytest.mark.setup_mode
def test_setup_page_is_public(tmp_db):
    client = TestClient(app)
    page = client.get("/setup")
    assert page.status_code == 200
    assert "Create the administrator" in page.text
    assert "Bootstrap token" in page.text
    token = peek_bootstrap_token()
    assert token
    assert token not in page.text


@pytest.mark.setup_mode
def test_invalid_setup_token_rejected(tmp_db):
    client = TestClient(app)
    client.get("/setup")
    response = client.post(
        "/setup",
        data={
            "bootstrap_token": "not-the-token",
            "email": "admin@lab.test",
            "password": "secret123",
            "name": "Admin",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert user_count() == 0


@pytest.mark.setup_mode
def test_valid_setup_token_creates_admin_and_cannot_be_reused(tmp_db):
    client = TestClient(app)
    client.get("/setup")
    token = peek_bootstrap_token()
    created = client.post(
        "/setup",
        data={
            "bootstrap_token": token,
            "email": "owner@lab.test",
            "password": "secret123",
            "name": "Owner",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    with session_scope() as session:
        row = authenticate_local(session, "owner@lab.test", "secret123")
        assert row is not None
        assert row.role == "admin"
        assert row.is_admin is True
    reuse = client.post(
        "/setup",
        data={
            "bootstrap_token": token,
            "email": "other@lab.test",
            "password": "secret123",
            "name": "Other",
        },
        follow_redirects=False,
    )
    assert reuse.status_code in {302, 303, 403}
    setup = client.get("/setup", follow_redirects=False)
    assert setup.status_code == 302
    assert "/login" in setup.headers["location"]


@pytest.mark.setup_mode
def test_subsequent_registrations_are_not_admin(tmp_db):
    from app.auth import ROLE_USER, create_local_user

    client = TestClient(app)
    client.get("/setup")
    token = ensure_bootstrap_token()
    client.post(
        "/setup",
        data={"bootstrap_token": token, "email": "owner@lab.test", "password": "secret123", "name": "Owner"},
    )
    with session_scope() as session:
        second = create_local_user(
            session, email="reader@lab.test", password="secret123", name="Reader", role=ROLE_USER
        )
        assert second.role == "user"
        assert second.is_admin is False


def test_ordinary_user_cannot_access_admin_routes(auth_client):
    client = auth_client
    client.post(
        "/account/users",
        data={"email": "reader@lab.test", "name": "Reader", "password": "secret123", "role": "user"},
    )
    client.post("/logout")
    client.post("/login", data={"email": "reader@lab.test", "password": "secret123", "next": "/"})
    settings = client.get("/settings", follow_redirects=False)
    assert settings.status_code == 302
    assert settings.headers["location"].endswith("/")
    api = client.get("/api/sources/1")
    assert api.status_code == 403

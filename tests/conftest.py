from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "pytest-session-secret-value-must-be-32bytes-min")
os.environ.setdefault("ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
os.environ.setdefault("TRUSTED_PROXY_IPS", "127.0.0.1")
os.environ.setdefault("LMS_SYNC_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient as _StarletteTestClient

from app.config import load_config
from app.database.connection import init_db, reset_engine
from app.database.settings_store import reset_settings_engine

TEST_ADMIN_EMAIL = "pytest-admin@localhost"
TEST_ADMIN_PASSWORD = "PytestAdmin1!"
TEST_ADMIN_NAME = "Pytest Admin"


class CsrfAwareTestClient(_StarletteTestClient):
    """Adds CSRF tokens to mutating test requests after the first GET."""

    def request(self, method, url, **kwargs):  # type: ignore[override]
        method_u = str(method or "GET").upper()
        if method_u in {"POST", "PUT", "PATCH", "DELETE"}:
            token = self.cookies.get("csrf_token")
            if not token:
                super().request("GET", "/login")
                token = self.cookies.get("csrf_token")
            headers = dict(kwargs.get("headers") or {})
            header_names = {str(key).lower() for key in headers}
            if token and "x-csrf-token" not in header_names:
                headers["X-CSRF-Token"] = token
                kwargs["headers"] = headers
            data = kwargs.get("data")
            if isinstance(data, dict) and token and "csrf_token" not in data:
                data = dict(data)
                data["csrf_token"] = token
                kwargs["data"] = data
        return super().request(method, url, **kwargs)


# Tests import TestClient from fastapi.testclient; patch after this module loads.
import fastapi.testclient as _ftc
import starlette.testclient as _stc

_ftc.TestClient = CsrfAwareTestClient  # type: ignore[misc]
_stc.TestClient = CsrfAwareTestClient  # type: ignore[misc]


_WEB_PATCH_MODULES = (
    "app.web",
    "app.web.dependencies",
    "app.web.middleware",
    "app.web.routes.auth",
    "app.web.routes.search",
    "app.web.routes.library",
    "app.web.routes.downloads",
    "app.web.routes.cfp",
    "app.web.routes.settings",
    "app.web.routes.admin",
    "app.web.routes.system",
    "app.auth",
)


def patch_web(monkeypatch, name: str, value) -> None:
    """Patch a name on the web package and every router that imported it."""
    for mod in _WEB_PATCH_MODULES:
        monkeypatch.setattr(f"{mod}.{name}", value, raising=False)


def login_admin(client: _StarletteTestClient, email: str = TEST_ADMIN_EMAIL, password: str = TEST_ADMIN_PASSWORD):
    client.get("/login")
    response = client.post("/login", data={"email": email, "password": password, "next": "/"}, follow_redirects=False)
    assert response.status_code in {200, 302, 303}, response.status_code
    # Pick up rotated CSRF cookie after session replacement.
    client.get("/")
    return client


@pytest.fixture
def tmp_db(tmp_path, monkeypatch, request):
    from app.auth import create_local_user, invalidate_user_count_cache
    from app.database.connection import session_scope
    from app.database.repository import invalidate_library_facets_cache
    from app.security.bootstrap import reset_bootstrap_state
    from app.security.login_limit import login_limiter

    db_path = tmp_path / "research.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MYSQL_HOST", "")
    monkeypatch.setenv("LMS_ROOT", "")
    monkeypatch.setenv("LMS_SYNC_ENABLED", "false")
    monkeypatch.setenv("SETTINGS_SQLITE_PATH", str(tmp_path / "settings.db"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    monkeypatch.setenv("GOOGLE_ADMIN_EMAILS", "")
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("SESSION_SECRET", "pytest-session-secret-value-must-be-32bytes-min")
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
    load_config.cache_clear()
    reset_engine()
    reset_settings_engine()
    invalidate_user_count_cache()
    invalidate_library_facets_cache()
    reset_bootstrap_state()
    login_limiter().reset()
    from app.services.provider_health import provider_health

    provider_health().reset()
    init_db(f"sqlite:///{db_path.as_posix()}")
    if request.node.get_closest_marker("setup_mode") is None:
        with session_scope() as session:
            create_local_user(
                session,
                email=TEST_ADMIN_EMAIL,
                password=TEST_ADMIN_PASSWORD,
                name=TEST_ADMIN_NAME,
                role="admin",
            )
        invalidate_user_count_cache()
    yield db_path
    reset_engine()
    reset_settings_engine()
    invalidate_user_count_cache()
    invalidate_library_facets_cache()
    reset_bootstrap_state()
    login_limiter().reset()
    from app.services.provider_health import provider_health

    provider_health().reset()
    load_config.cache_clear()


@pytest.fixture
def auth_client(tmp_db):
    from app.web import app

    client = CsrfAwareTestClient(app)
    login_admin(client)
    return client

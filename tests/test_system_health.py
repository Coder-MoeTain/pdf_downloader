"""Admin system health page and metrics API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.web import app
from tests.test_auth import _enable_google


def test_system_health_collects_metrics():
    from app.services.system_health import collect_system_health

    snap = collect_system_health(process_limit=5)
    assert snap["ok"] is True
    assert "cpu" in snap and snap["cpu"]["percent"] >= 0
    assert "memory" in snap and snap["memory"]["total"] > 0
    assert "storage" in snap
    assert "network" in snap
    assert "processes" in snap
    assert isinstance(snap["processes"]["top"], list)
    assert "history" in snap


def test_non_admin_cannot_open_system_health(tmp_db, monkeypatch):
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
    page = client.get("/system")
    assert page.status_code == 302
    assert page.headers["location"].endswith("/")
    api = client.get("/api/system-health")
    assert api.status_code == 403
    home = client.get("/", follow_redirects=True)
    assert 'href="/system"' not in home.text


def test_admin_can_open_system_health(tmp_db, monkeypatch):
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
    page = client.get("/system")
    assert page.status_code == 200
    assert "System health" in page.text
    assert "sys-kpi-grid" in page.text
    assert "sys-status-banner" in page.text
    assert "/static/health.js" in page.text
    assert "/static/system.css" in page.text
    api = client.get("/api/system-health")
    assert api.status_code == 200
    body = api.json()
    assert body["ok"] is True
    assert "cpu" in body
    home = client.get("/")
    assert 'href="/system"' in home.text

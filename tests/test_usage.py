from fastapi.testclient import TestClient

from app.services import usage
from app.web import app


def test_usage_events_and_online_presence(tmp_db):
    usage.reset_presence()
    usage.touch_presence(
        {"id": 7, "name": "Ada", "email": "ada@example.com", "role": "admin"},
        path="/search",
    )
    online = usage.list_online()
    assert online
    assert online[0]["email"] == "ada@example.com"
    assert online[0]["path"] == "/search"
    usage.log_event(action="search", detail="machine learning", user_label="Ada", ip="127.0.0.1")
    events = usage.list_events()
    assert events[0]["action"] == "search"
    assert events[0]["detail"] == "machine learning"
    usage.drop_presence(7)
    assert all(row["id"] != 7 for row in usage.list_online())


def test_settings_activity_shows_online_user_and_login(tmp_db):
    usage.reset_presence()
    client = TestClient(app)
    signed = client.post(
        "/login",
        data={"email": "admin@localhost", "password": "Admin@123", "name": "Administrator"},
        follow_redirects=False,
    )
    assert signed.status_code == 303
    page = client.get("/settings?section=activity")
    assert page.status_code == 200
    assert "Online" in page.text
    assert "Recent activity" in page.text
    assert "Administrator" in page.text or "admin@localhost" in page.text
    assert "Signed in" in page.text
    payload = client.get("/api/activity").json()
    assert payload["online_count"] >= 1
    assert any(row["action"] == "login" for row in payload["events"])

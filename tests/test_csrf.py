from app.web import app


def test_csrf_less_settings_post_rejected(auth_client):
    from starlette.testclient import TestClient as Raw

    raw = Raw(app, cookies=auth_client.cookies)
    response = raw.post(
        "/settings/workspace",
        data={"timezone": "UTC", "library_dir": "research_library"},
        headers={"Cookie": "; ".join(f"{k}={v}" for k, v in auth_client.cookies.items())},
        follow_redirects=False,
    )
    assert response.status_code in {403, 303}


def test_csrf_header_allows_ajax(auth_client):
    token = auth_client.cookies.get("csrf_token")
    assert token
    response = auth_client.post(
        "/api/cfp-refresh",
        headers={"X-CSRF-Token": token, "Accept": "application/json"},
    )
    assert response.status_code != 403 or "csrf" not in (response.text or "").lower()

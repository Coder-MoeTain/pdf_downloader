from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.web import app


def _proc(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_git_pull_fast_forward_only(monkeypatch):
    from app.utils import git_update

    calls: list[tuple[str, ...]] = []

    def fake_git(*args, **kwargs):
        calls.append(args)
        if args == ("rev-parse", "--is-inside-work-tree"):
            return _proc("true\n")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return _proc("main\n")
        if args[:2] == ("rev-parse", "--short"):
            return _proc("abc1234\n")
        if args == ("rev-parse", "HEAD"):
            return _proc("abc1234full\n")
        if args[0] == "log":
            return _proc("Latest change\n")
        if args[:2] == ("remote", "get-url"):
            return _proc("https://token:x@github.com/org/repo.git\n")
        if args[0] == "status":
            return _proc("")
        if args[0] == "fetch":
            return _proc("")
        if args[:2] == ("pull", "--ff-only"):
            return _proc("Already up to date.\n")
        return _proc("", "unexpected", 1)

    monkeypatch.setattr(git_update, "run_git", fake_git)
    result = git_update.git_pull()
    assert result["ok"] is True
    assert result["already_current"] is True
    assert ("pull", "--ff-only") in calls
    status = git_update.git_status()
    assert status["remote"] == "https://github.com/org/repo.git"


def test_git_pull_blocked_when_dirty(monkeypatch):
    from app.utils import git_update
    from app.utils.git_update import GitUpdateError

    def fake_git(*args, **kwargs):
        if args == ("rev-parse", "--is-inside-work-tree"):
            return _proc("true\n")
        if args[0] == "status":
            return _proc(" M app/web/__init__.py\n")
        return _proc("main\n")

    monkeypatch.setattr(git_update, "run_git", fake_git)
    try:
        git_update.git_pull()
        raise AssertionError("expected GitUpdateError")
    except GitUpdateError as exc:
        assert "uncommitted" in str(exc)


def test_settings_updates_page_has_git_pull(tmp_db, monkeypatch):
    monkeypatch.setattr(
        "app.web.git_status",
        lambda: {
            "ok": True,
            "error": "",
            "branch": "main",
            "commit": "abc",
            "short": "abc1234",
            "subject": "seed admin",
            "remote": "https://github.com/org/repo.git",
            "dirty": False,
        },
    )
    client = TestClient(app)
    page = client.get("/settings?section=updates")
    assert page.status_code == 200
    assert "Git pull" in page.text
    assert 'action="/settings/update"' in page.text
    assert "abc1234" in page.text


def test_non_admin_cannot_git_pull(tmp_db, monkeypatch):
    user = {
        "id": 1,
        "email": "reader@gmail.com",
        "name": "Reader",
        "picture": "",
        "role": "user",
        "is_admin": False,
        "has_password": True,
    }
    monkeypatch.setattr("app.web.google_login_enabled", lambda: True)
    monkeypatch.setattr("app.auth.google_login_enabled", lambda: True)
    monkeypatch.setattr("app.web.auth_required", lambda: True)
    monkeypatch.setattr("app.auth.auth_required", lambda: True)
    monkeypatch.setattr("app.web.current_user", lambda _request: user)
    monkeypatch.setattr("app.auth.current_user", lambda _request: user)
    monkeypatch.setattr(
        "app.web.user_is_admin",
        lambda _request: False,
    )
    monkeypatch.setattr(
        "app.web.user_role",
        lambda _value: "user",
    )
    pulled = False

    def boom():
        nonlocal pulled
        pulled = True
        raise AssertionError("must not pull")

    monkeypatch.setattr("app.web.git_pull", boom)
    client = TestClient(app, follow_redirects=False)
    response = client.post("/settings/update")
    assert response.status_code == 302
    assert response.headers["location"].endswith("/")
    assert pulled is False

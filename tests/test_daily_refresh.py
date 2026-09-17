"""Daily 06:00 refresh for GitHub projects and Call for Papers."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.database.settings_repository import load_daily_refresh_last_run, mark_daily_refresh_run
from app.services.daily_refresh import daily_refresh_is_due, next_daily_refresh_at, run_daily_refresh_once
from app.utils.time import utc_now


YANGON = ZoneInfo("Asia/Yangon")


def test_daily_refresh_is_due_at_six_local():
    last = datetime(2026, 9, 16, 6, 5, tzinfo=YANGON)
    before = datetime(2026, 9, 17, 5, 59, tzinfo=YANGON)
    at_six = datetime(2026, 9, 17, 6, 0, tzinfo=YANGON)
    later = datetime(2026, 9, 17, 8, 30, tzinfo=YANGON)
    assert daily_refresh_is_due(now=before, last_run=last) is False
    assert daily_refresh_is_due(now=at_six, last_run=last) is True
    assert daily_refresh_is_due(now=later, last_run=last) is True
    assert daily_refresh_is_due(now=later, last_run=at_six) is False


def test_daily_refresh_catch_up_after_six_when_never_run():
    morning = datetime(2026, 9, 17, 9, 15, tzinfo=YANGON)
    assert daily_refresh_is_due(now=morning, last_run=None) is True
    assert daily_refresh_is_due(now=datetime(2026, 9, 17, 5, 0, tzinfo=YANGON), last_run=None) is False


def test_next_daily_refresh_at_rolls_to_tomorrow():
    now = datetime(2026, 9, 17, 7, 0, tzinfo=YANGON)
    last = datetime(2026, 9, 17, 6, 1, tzinfo=YANGON)
    nxt = next_daily_refresh_at(now=now, last_run=last)
    assert nxt.hour == 6
    assert nxt.date().isoformat() == "2026-09-18"


def test_run_daily_refresh_once_invokes_github_and_cfp(tmp_db, monkeypatch):
    calls: list[str] = []

    def fake_github(*, claim=True):
        calls.append("github")
        return {"repos": 10, "categories": 10}

    def fake_cfp(*, force=False, claim=True):
        calls.append(f"cfp:{force}")
        return {"upserted": 4}

    monkeypatch.setattr("app.services.github_service.refresh_github_repos", fake_github)
    monkeypatch.setattr("app.services.cfp_service.refresh_cfps", fake_cfp)
    monkeypatch.setattr(
        "app.services.daily_refresh.daily_refresh_is_due",
        lambda **kwargs: True,
    )

    result = run_daily_refresh_once()
    assert result["due"] is True
    assert calls == ["github", "cfp:True"]
    assert load_daily_refresh_last_run()

    monkeypatch.setattr(
        "app.services.daily_refresh.daily_refresh_is_due",
        lambda **kwargs: False,
    )
    skipped = run_daily_refresh_once()
    assert skipped["due"] is False
    assert calls == ["github", "cfp:True"]


def test_mark_daily_refresh_run_roundtrip(tmp_db):
    assert load_daily_refresh_last_run() == ""
    stamp = utc_now()
    mark_daily_refresh_run(stamp)
    assert load_daily_refresh_last_run().startswith(stamp.isoformat()[:19])


def test_daily_refresh_force_runs_even_when_not_due(tmp_db, monkeypatch):
    mark_daily_refresh_run(datetime(2026, 9, 17, 6, 10, tzinfo=UTC))
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.github_service.refresh_github_repos",
        lambda **kwargs: calls.append("github") or {"repos": 1},
    )
    monkeypatch.setattr(
        "app.services.cfp_service.refresh_cfps",
        lambda **kwargs: calls.append("cfp") or {"upserted": 1},
    )
    result = run_daily_refresh_once(force=True)
    assert result["due"] is True
    assert calls == ["github", "cfp"]

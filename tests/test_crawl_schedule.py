"""Tests for scheduled crawler settings and due logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.database.settings_repository import load_crawl_schedule, save_crawl_schedule_settings
from app.services.crawl_schedule import next_run_at, run_scheduled_crawl_once, schedule_is_due


def test_save_and_load_crawl_schedule(tmp_db):
    save_crawl_schedule_settings(
        {
            "enabled": True,
            "interval_minutes": 60,
            "sources": ["openalex", "pubmed"],
            "query": "satellite",
            "skip_existing": True,
            "open_access_only": False,
            "download": False,
            "pdfs_only": False,
            "max_pages": 5,
            "max_papers": 200,
        }
    )
    schedule = load_crawl_schedule()
    assert schedule["enabled"] is True
    assert schedule["interval_minutes"] == 60
    assert schedule["sources"] == ["openalex", "pubmed"]
    assert schedule["query"] == "satellite"
    assert schedule["max_pages"] == 5
    assert schedule["last_run"]


def test_schedule_is_due_respects_interval(tmp_db):
    now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    settings = {
        "enabled": True,
        "interval_minutes": 60,
        "sources": ["openalex"],
        "last_run": (now - timedelta(minutes=30)).isoformat(),
    }
    assert schedule_is_due(settings, now=now) is False
    settings["last_run"] = (now - timedelta(minutes=61)).isoformat()
    assert schedule_is_due(settings, now=now) is True
    assert next_run_at(settings) is not None


def test_run_scheduled_crawl_skips_when_not_due(tmp_db, monkeypatch):
    save_crawl_schedule_settings(
        {
            "enabled": True,
            "interval_minutes": 60,
            "sources": ["openalex"],
            "skip_existing": True,
            "max_pages": 2,
            "max_papers": 50,
        }
    )
    calls: list[str] = []

    def fake_enqueue(*, user_id, filters):
        calls.append(filters.source)
        return 1

    monkeypatch.setattr("app.services.crawl_schedule.enqueue_crawl", fake_enqueue)
    monkeypatch.setattr("app.services.crawl_schedule._crawlable_slugs", lambda: {"openalex"})
    result = run_scheduled_crawl_once()
    assert result["due"] is False
    assert calls == []


def test_run_scheduled_crawl_queues_due_sources(tmp_db, monkeypatch):
    save_crawl_schedule_settings(
        {
            "enabled": True,
            "interval_minutes": 60,
            "sources": ["openalex", "pubmed"],
            "skip_existing": True,
            "max_pages": 2,
            "max_papers": 50,
        }
    )
    from app.database.settings_repository import mark_crawl_schedule_run

    mark_crawl_schedule_run(datetime.now(timezone.utc) - timedelta(hours=2))
    calls: list[str] = []

    def fake_enqueue(*, user_id, filters):
        calls.append(filters.source)
        return len(calls)

    monkeypatch.setattr("app.services.crawl_schedule.enqueue_crawl", fake_enqueue)
    monkeypatch.setattr("app.services.crawl_schedule._crawlable_slugs", lambda: {"openalex", "pubmed"})
    monkeypatch.setattr(
        "app.services.crawl_schedule.active_crawl_job_for_source",
        lambda session, source: None,
    )
    result = run_scheduled_crawl_once()
    assert result["due"] is True
    assert calls == ["openalex", "pubmed"]

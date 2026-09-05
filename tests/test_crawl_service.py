"""Tests for source crawler helpers."""

from __future__ import annotations

import json

from app.models.crawl import BrowsePage, CrawlFilters
from app.models.paper import PaperRecord, PaperStatus
from app.services.crawl_queue import filters_from_dict
from app.services.crawl_service import has_downloadable_pdf


def test_crawl_filters_from_dict():
    payload = {
        "source": "openalex",
        "query": "security",
        "skip_existing": True,
        "page_size": 50,
        "max_pages": 10,
        "max_papers": 1000,
        "pdfs_only": True,
    }
    filters = filters_from_dict(payload)
    assert filters.source == "openalex"
    assert filters.query == "security"
    assert filters.skip_existing is True
    assert filters.page_size == 50
    assert filters.pdfs_only is True


def test_crawl_filters_defaults_full_harvest():
    filters = filters_from_dict({"source": "openalex"})
    assert filters.max_pages == 0
    assert filters.max_papers == 50000
    assert filters.pdfs_only is False


def test_has_downloadable_pdf():
    ok = PaperRecord(
        title="Open paper",
        pdf_url="https://arxiv.org/pdf/1234.5678.pdf",
        status=PaperStatus.OA_AVAILABLE,
        source_provider="arxiv",
    )
    blocked = PaperRecord(
        title="Paywalled",
        doi="10.1000/example",
        status=PaperStatus.PAYWALLED,
        source_provider="crossref",
    )
    assert has_downloadable_pdf(ok) is True
    assert has_downloadable_pdf(blocked) is False


def test_crawl_filters_default_download_enabled():
    filters = CrawlFilters(source="openalex")
    assert filters.download is True


def test_enqueue_payload_serializes():
    filters = CrawlFilters(source="arxiv", skip_existing=True, max_pages=5)
    raw = json.dumps(
        {
            "source": filters.source,
            "skip_existing": filters.skip_existing,
            "max_pages": filters.max_pages,
        }
    )
    restored = filters_from_dict(json.loads(raw))
    assert restored.source == "arxiv"
    assert restored.skip_existing is True
    assert restored.max_pages == 5


def test_browse_page_defaults():
    page = BrowsePage(records=[], next_cursor=None, has_more=False)
    assert page.page_number == 1
    assert page.total_results is None


def test_crawl_source_rows_many_crawlable(tmp_db):
    from app.web import _crawl_source_rows

    rows = _crawl_source_rows()
    crawlable = [row for row in rows if row["supports_browse"]]
    assert len(crawlable) >= 15


def test_crawler_page_renders_for_admin(tmp_db):
    from fastapi.testclient import TestClient

    from app.auth import create_local_user
    from app.database.connection import session_scope
    from app.web import app

    with session_scope() as session:
        create_local_user(
            session,
            email="crawler@test.local",
            password="password1",
            name="Crawler Admin",
            role="admin",
        )
    client = TestClient(app, follow_redirects=False)
    login = client.post(
        "/login",
        data={"email": "crawler@test.local", "password": "password1", "next": "/crawler"},
    )
    assert login.status_code == 303
    page = client.get("/crawler")
    assert page.status_code == 200
    assert "Source crawler" in page.text
    assert 'action="/crawler"' in page.text
    assert 'name="sources"' in page.text
    assert "Select all" in page.text
    assert "Search only" in page.text
    assert "crawl-source-panel" in page.text


def test_crawl_multi_source_enqueue(tmp_db):
    from fastapi.testclient import TestClient

    from app.auth import create_local_user
    from app.database.connection import session_scope
    from app.database.models import CrawlJob
    from app.web import app
    from sqlalchemy import func, select

    with session_scope() as session:
        create_local_user(
            session,
            email="multi-crawl@test.local",
            password="password1",
            name="Multi Crawl Admin",
            role="admin",
        )
    client = TestClient(app, follow_redirects=False)
    login = client.post(
        "/login",
        data={"email": "multi-crawl@test.local", "password": "password1", "next": "/crawler"},
    )
    assert login.status_code == 303
    resp = client.post(
        "/crawler",
        data={
            "sources": ["openalex", "crossref"],
            "skip_existing": "1",
            "download": "1",
            "max_pages": "1",
            "max_papers": "10",
        },
    )
    assert resp.status_code == 303
    assert "job=" in resp.headers["location"]
    with session_scope() as session:
        count = session.scalar(select(func.count()).select_from(CrawlJob)) or 0
        sources = session.scalars(select(CrawlJob.source)).all()
    assert count == 2
    assert set(sources) == {"openalex", "crossref"}


def test_crawl_submit_requires_selection(tmp_db):
    from fastapi.testclient import TestClient

    from app.auth import create_local_user
    from app.database.connection import session_scope
    from app.web import app

    with session_scope() as session:
        create_local_user(
            session,
            email="empty-crawl@test.local",
            password="password1",
            name="Empty Crawl Admin",
            role="admin",
        )
    client = TestClient(app, follow_redirects=True)
    client.post(
        "/login",
        data={"email": "empty-crawl@test.local", "password": "password1", "next": "/crawler"},
    )
    page = client.post("/crawler", data={"max_pages": "1"})
    assert page.status_code == 200
    assert "Select at least one source" in page.text


def test_enqueue_registers_crawl_progress(tmp_db):
    from app.services.crawl_queue import enqueue_crawl
    from app.services.progress import crawl_job_registry
    from app.models.crawl import CrawlFilters

    crawl_job_registry.clear_all()
    job_id = enqueue_crawl(user_id=None, filters=CrawlFilters(source="openalex"))
    snap = crawl_job_registry.snapshot(job_id)
    assert snap is not None
    assert snap["active"] is True
    assert snap["kind"] == "crawl"
    assert snap["logs"]
    assert snap["query"] == "openalex"


def test_crawl_progress_snapshot_db_fallback(tmp_db):
    from app.database.connection import session_scope
    from app.database.repository import enqueue_crawl_job, crawl_queue_position
    from app.services.crawl_queue import crawl_progress_snapshot, db_crawl_progress_snapshot

    with session_scope() as session:
        job = enqueue_crawl_job(
            session,
            user_id=None,
            source="arxiv",
            filters={"source": "arxiv"},
        )
        job_id = job.id
        pos = crawl_queue_position(session, job_id)
    snap = db_crawl_progress_snapshot(job, position=pos)
    assert snap["job_id"] == job_id
    assert snap["kind"] == "crawl"
    assert any("Crawl queued" in entry["message"] for entry in snap["logs"])
    assert crawl_progress_snapshot(job_id) is not None


def test_crawl_progress_api_with_pending_job(tmp_db):
    from fastapi.testclient import TestClient

    from app.auth import create_local_user
    from app.database.connection import session_scope
    from app.database.repository import enqueue_crawl_job
    from app.web import app

    with session_scope() as session:
        create_local_user(session, email="crawl-api@test.local", password="password1", name="Crawl", role="admin")
        job = enqueue_crawl_job(
            session,
            user_id=None,
            source="openalex",
            filters={"source": "openalex"},
        )
        job_id = job.id
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"email": "crawl-api@test.local", "password": "password1", "next": "/crawler"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    resp = client.get(f"/api/crawl-progress?job_id={job_id}")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["job_id"] == job_id
    assert payload["kind"] == "crawl"
    assert payload["logs"]


def test_run_crawl_preserves_queued_logs():
    from app.services.progress import crawl_job_registry

    progress = crawl_job_registry.register_queued_crawl(42, "openalex")
    progress.mark_crawl_started("openalex")
    snap = progress.snapshot()
    assert len(snap["logs"]) >= 2
    assert any("Crawl queued" in entry["message"] for entry in snap["logs"])
    assert any("starting" in entry["message"].lower() for entry in snap["logs"])

"""Reports page and persisted search/crawl job stats."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.database.connection import session_scope
from app.database.models import CrawlJob, SearchJob, User
from app.database.repository import (
    complete_crawl_job,
    complete_search_job,
    count_search_jobs,
    crawl_job_keyword,
    enqueue_crawl_job,
    enqueue_search_job,
    list_crawl_jobs,
    list_search_jobs,
)
from app.web import app
from app.web.ui import reports_href


def test_complete_search_job_stores_download_stats(tmp_db):
    with session_scope() as session:
        job = enqueue_search_job(session, user_id=None, query="yolo", filters={"query": "yolo"})
        job_id = job.id
        complete_search_job(
            session,
            job_id,
            status="completed",
            papers_found=12,
            pdfs_downloaded=4,
            pdfs_failed=2,
        )
    with session_scope() as session:
        row = session.get(SearchJob, job_id)
        assert row is not None
        assert row.papers_found == 12
        assert row.pdfs_downloaded == 4
        assert row.pdfs_failed == 2
        assert row.status == "completed"


def test_complete_crawl_job_stores_download_stats(tmp_db):
    with session_scope() as session:
        job = enqueue_crawl_job(
            session,
            user_id=None,
            source="arxiv",
            filters={"source": "arxiv", "query": "object detection"},
        )
        job_id = job.id
        complete_crawl_job(
            session,
            job_id,
            status="completed",
            papers_found=30,
            pdfs_downloaded=8,
            pdfs_failed=1,
        )
    with session_scope() as session:
        row = session.get(CrawlJob, job_id)
        assert row is not None
        assert crawl_job_keyword(row) == "object detection"
        assert row.papers_found == 30
        assert row.pdfs_downloaded == 8
        assert row.pdfs_failed == 1


def test_list_search_jobs_filters_by_user_and_query(tmp_db):
    with session_scope() as session:
        alice = User(google_id="g-alice", email="alice@example.com", name="Alice", role="user", is_admin=False)
        bob = User(google_id="g-bob", email="bob@example.com", name="Bob", role="user", is_admin=False)
        session.add_all([alice, bob])
        session.flush()
        enqueue_search_job(session, user_id=alice.id, query="neural nets", filters={})
        enqueue_search_job(session, user_id=bob.id, query="yolo detection", filters={})
        alice_id = alice.id
    with session_scope() as session:
        rows = list_search_jobs(session, user_id=alice_id, q="neural", with_user=True)
        assert len(rows) == 1
        assert rows[0].query == "neural nets"
        assert count_search_jobs(session, user_id=alice_id) == 1
        assert len(list_crawl_jobs(session)) == 0


def test_reports_href_defaults():
    assert reports_href() == "/reports"
    assert reports_href({"tab": "crawl"}) == "/reports?tab=crawl"
    assert "q=yolo" in reports_href(q="yolo")


def test_reports_page_lists_search_and_crawl(tmp_db, monkeypatch):
    with session_scope() as session:
        admin = User(google_id="g-admin", email="admin@example.com", name="Admin", role="admin", is_admin=True)
        session.add(admin)
        session.flush()
        admin_id = admin.id
        search = enqueue_search_job(session, user_id=admin_id, query="satellite imagery", filters={})
        complete_search_job(
            session,
            search.id,
            papers_found=5,
            pdfs_downloaded=3,
            pdfs_failed=1,
        )
        crawl = enqueue_crawl_job(
            session,
            user_id=admin_id,
            source="openalex",
            filters={"query": "remote sensing"},
        )
        complete_crawl_job(
            session,
            crawl.id,
            papers_found=9,
            pdfs_downloaded=2,
            pdfs_failed=0,
        )

    payload = {
        "id": admin_id,
        "email": "admin@example.com",
        "name": "Admin",
        "role": "admin",
        "is_admin": True,
    }
    monkeypatch.setattr("app.web.auth_required", lambda: True)
    monkeypatch.setattr("app.auth.auth_required", lambda: True)
    monkeypatch.setattr("app.web.current_user", lambda _request: payload)
    monkeypatch.setattr("app.auth.current_user", lambda _request: payload)
    monkeypatch.setattr("app.web.user_is_admin", lambda _request: True)
    monkeypatch.setattr("app.auth.user_is_admin", lambda _request: True)

    client = TestClient(app)
    search_resp = client.get("/reports")
    assert search_resp.status_code == 200
    assert "satellite imagery" in search_resp.text
    assert "Downloaded" in search_resp.text

    crawl_resp = client.get("/reports?tab=crawl")
    assert crawl_resp.status_code == 200
    assert "remote sensing" in crawl_resp.text

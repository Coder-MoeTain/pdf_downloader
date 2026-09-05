"""Tests for the persistent search queue."""

from __future__ import annotations

from app.database.connection import session_scope
from app.database.models import User
from app.database.repository import (
    active_search_job_for_user,
    claim_next_search_job,
    enqueue_search_job,
    get_search_job,
    queue_position,
    search_jobs_grouped_by_user,
)
from app.services.search_queue import cancel_search
from app.database.source_catalog import DEFAULT_ENABLED_SOURCE_SLUGS


def _create_user(session, email: str) -> User:
    row = User(
        google_id=f"local:{email}",
        email=email,
        name=email.split("@")[0],
        role="user",
    )
    session.add(row)
    session.flush()
    return row


def test_enqueue_and_claim_per_user(tmp_db):
    with session_scope() as session:
        alice = _create_user(session, "alice@test.local")
        bob = _create_user(session, "bob@test.local")
        j1 = enqueue_search_job(session, user_id=alice.id, query="topic A", filters={"query": "topic A"})
        j2 = enqueue_search_job(session, user_id=bob.id, query="topic B", filters={"query": "topic B"})
        j3 = enqueue_search_job(session, user_id=alice.id, query="topic C", filters={"query": "topic C"})

    with session_scope() as session:
        first = claim_next_search_job(session)
        second = claim_next_search_job(session)
        assert first is not None
        assert second is not None
        assert {first.user_id, second.user_id} == {alice.id, bob.id}
        assert first.status == "running"
        assert second.status == "running"

        third = claim_next_search_job(session)
        assert third is None

        pos = queue_position(session, j3.id)
        assert pos == 1


def test_claim_allows_multiple_jobs_per_user(tmp_db):
    with session_scope() as session:
        alice = _create_user(session, "alice2@test.local")
        enqueue_search_job(session, user_id=alice.id, query="A1", filters={"query": "A1"})
        enqueue_search_job(session, user_id=alice.id, query="A2", filters={"query": "A2"})
        first = claim_next_search_job(session, max_per_user=2)
        second = claim_next_search_job(session, max_per_user=2)
        third = claim_next_search_job(session, max_per_user=2)
        assert first is not None
        assert second is not None
        assert first.user_id == alice.id
        assert second.user_id == alice.id
        assert third is None


def test_queue_grouped_by_username(tmp_db):
    with session_scope() as session:
        user = _create_user(session, "carol@test.local")
        enqueue_search_job(session, user_id=user.id, query="queued", filters={"query": "queued"})
        grouped = search_jobs_grouped_by_user(session)
    assert "carol@test.local" in grouped
    assert grouped["carol@test.local"][0].query == "queued"


def test_active_job_for_user(tmp_db):
    with session_scope() as session:
        user = _create_user(session, "dave@test.local")
        job = enqueue_search_job(session, user_id=user.id, query="run", filters={"query": "run"})
        claimed = claim_next_search_job(session)
        assert claimed.id == job.id
        active = active_search_job_for_user(session, user.id)
        assert active is not None
        assert active.id == job.id


def test_cancel_pending_search(tmp_db):
    with session_scope() as session:
        user = _create_user(session, "erin@test.local")
        job = enqueue_search_job(session, user_id=user.id, query="stop me", filters={"query": "stop me"})
        job_id = job.id
        user_id = user.id
    was = cancel_search(job_id, user_id=user_id)
    assert was == "pending"
    with session_scope() as session:
        row = get_search_job(session, job_id)
        assert row.status == "cancelled"
        assert claim_next_search_job(session) is None


def test_cancel_search_rejects_other_user(tmp_db):
    with session_scope() as session:
        owner = _create_user(session, "owner@test.local")
        other = _create_user(session, "other@test.local")
        job = enqueue_search_job(session, user_id=owner.id, query="private", filters={"query": "private"})
        job_id = job.id
        other_id = other.id
    try:
        cancel_search(job_id, user_id=other_id, is_admin=False)
        raise AssertionError("expected PermissionError")
    except PermissionError:
        pass
    was = cancel_search(job_id, user_id=other_id, is_admin=True)
    assert was == "pending"


def test_cancel_running_search_updates_job_and_progress(tmp_db):
    from app.services.progress import job_registry

    with session_scope() as session:
        user = _create_user(session, "runner@test.local")
        job = enqueue_search_job(session, user_id=user.id, query="running stop", filters={"query": "running stop"})
        claimed = claim_next_search_job(session)
        assert claimed is not None
        job_id = claimed.id
        user_id = user.id
    progress = job_registry.create(job_id)
    progress.start_search("running stop")
    was = cancel_search(job_id, user_id=user_id)
    assert was == "running"
    assert progress.is_cancelled() is True
    with session_scope() as session:
        row = get_search_job(session, job_id)
        assert row is not None
        assert row.status == "cancelled"


def test_search_providers_stop_while_waiting():
    import asyncio

    from app.models.search import SearchFilters, SearchStats
    from app.services.progress import ProgressTracker
    from app.services.search_service import SearchCancelled, SearchService

    class SlowProvider:
        name = "slow"
        display_name = "Slow"

        async def search(self, query, filters):
            await asyncio.sleep(5)
            return []

    async def main():
        tracker = ProgressTracker()
        tracker.start_search("wait")
        service = SearchService(progress=tracker)
        stats = SearchStats(query="wait")

        async def cancel_soon():
            await asyncio.sleep(0.05)
            tracker.request_cancel()

        asyncio.create_task(cancel_soon())
        try:
            await service._search_providers([SlowProvider()], "wait", SearchFilters(query="wait"), stats)
            raise AssertionError("expected SearchCancelled")
        except SearchCancelled:
            pass
        assert tracker.is_cancelled() is True

    asyncio.run(main())


def test_search_skips_slow_providers(tmp_db):
    import asyncio
    import time

    from app.config import load_config
    from app.models.paper import PaperRecord
    from app.models.search import SearchFilters, SearchStats
    from app.services.progress import ProgressTracker
    from app.services.search_service import SearchService

    class FastProvider:
        name = "fast"
        display_name = "Fast"

        async def search(self, query, filters):
            return [PaperRecord(title="Quick paper")]

    class SlowProvider:
        name = "slow"
        display_name = "Slow"

        async def search(self, query, filters):
            await asyncio.sleep(5)
            return [PaperRecord(title="Late paper")]

    async def main():
        tracker = ProgressTracker()
        tracker.start_search("wait")
        cfg = load_config()
        cfg.provider_timeout_seconds = 0.2
        cfg.provider_phase_seconds = 0.4
        service = SearchService(config=cfg, progress=tracker)
        stats = SearchStats(query="wait")
        started = time.monotonic()
        raw = await service._search_providers(
            [FastProvider(), SlowProvider()],
            "wait",
            SearchFilters(query="wait"),
            stats,
        )
        elapsed = time.monotonic() - started
        assert elapsed < 2.0
        assert any(p.title == "Quick paper" for p in raw)
        assert all(p.title != "Late paper" for p in raw)
        assert stats.provider_counts.get("Fast") == 1

    asyncio.run(main())


def test_search_page_shows_stop_and_cancels_pending(tmp_db):
    from fastapi.testclient import TestClient

    from app.auth import create_local_user
    from app.web import app

    with session_scope() as session:
        user = create_local_user(session, email="stopper@test.local", password="password1", name="Stopper")
        job = enqueue_search_job(session, user_id=user.id, query="pending stop", filters={"query": "pending stop"})
        job_id = job.id
    client = TestClient(app, follow_redirects=False)
    login = client.post(
        "/login",
        data={"email": "stopper@test.local", "password": "password1", "next": "/search"},
    )
    assert login.status_code == 303
    page = client.get("/search")
    assert page.status_code == 200
    assert f"/search/jobs/{job_id}/stop" in page.text
    assert "pending" in page.text.lower()
    stopped = client.post(f"/search/jobs/{job_id}/stop")
    assert stopped.status_code == 303
    with session_scope() as session:
        row = get_search_job(session, job_id)
        assert row is not None
        assert row.status == "cancelled"


def test_stop_running_job_json(tmp_db):
    from fastapi.testclient import TestClient

    from app.auth import create_local_user
    from app.web import app

    with session_scope() as session:
        user = create_local_user(session, email="jsonstop@test.local", password="password1", name="Json")
        job = enqueue_search_job(session, user_id=user.id, query="json stop", filters={"query": "json stop"})
        claimed = claim_next_search_job(session)
        assert claimed is not None
        job_id = claimed.id
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"email": "jsonstop@test.local", "password": "password1", "next": "/search"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    stopped = client.post(
        f"/search/jobs/{job_id}/stop",
        headers={"Accept": "application/json"},
    )
    assert stopped.status_code == 200
    payload = stopped.json()
    assert payload["ok"] is True
    assert payload["was"] == "running"
    with session_scope() as session:
        row = get_search_job(session, job_id)
        assert row is not None
        assert row.status == "cancelled"


def test_enqueue_registers_progress(tmp_db):
    from app.services.progress import job_registry
    from app.services.search_queue import enqueue_search

    job_registry.clear_all()
    with session_scope() as session:
        user = _create_user(session, "queue@test.local")
        user_id = user.id
    job_id = enqueue_search(user_id=user_id, query="queued topic", filters={"query": "queued topic"})
    snap = job_registry.snapshot(job_id)
    assert snap is not None
    assert snap["active"] is True
    assert snap["kind"] == "search"
    assert snap["logs"]
    assert snap["query"] == "queued topic"


def test_search_progress_snapshot_db_fallback(tmp_db):
    from app.services.search_queue import db_job_progress_snapshot, search_progress_snapshot

    with session_scope() as session:
        user = _create_user(session, "fallback@test.local")
        job = enqueue_search_job(session, user_id=user.id, query="db fallback", filters={"query": "db fallback"})
        job_id = job.id
        pos = queue_position(session, job_id)
    snap = db_job_progress_snapshot(job, position=pos)
    assert snap["job_id"] == job_id
    assert snap["active"] is True
    assert any("Search queued" in entry["message"] for entry in snap["logs"])
    assert search_progress_snapshot(job_id) is not None


def test_run_job_preserves_queued_logs(tmp_db):
    from app.services.progress import job_registry

    progress = job_registry.register_queued(99, "keep logs")
    progress.mark_search_started("keep logs")
    snap = progress.snapshot()
    assert len(snap["logs"]) >= 2
    assert any("Search queued" in entry["message"] for entry in snap["logs"])
    assert any("starting" in entry["message"].lower() for entry in snap["logs"])


def test_search_progress_api_with_pending_job(tmp_db):
    from fastapi.testclient import TestClient

    from app.auth import create_local_user
    from app.web import app

    with session_scope() as session:
        user = create_local_user(session, email="api@test.local", password="password1", name="Api")
        job = enqueue_search_job(session, user_id=user.id, query="api pending", filters={"query": "api pending"})
        job_id = job.id
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"email": "api@test.local", "password": "password1", "next": "/search"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    resp = client.get(f"/api/search-progress?job_id={job_id}")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["job_id"] == job_id
    assert payload["kind"] == "search"
    assert payload["logs"]


def test_top20_default_sources():
    assert len(DEFAULT_ENABLED_SOURCE_SLUGS) == 20
    assert "openalex" in DEFAULT_ENABLED_SOURCE_SLUGS
    assert "datacite" not in DEFAULT_ENABLED_SOURCE_SLUGS

"""Tests for the persistent search queue."""

from __future__ import annotations

from app.database.connection import session_scope
from app.database.models import User
from app.database.repository import (
    active_search_job_for_user,
    claim_next_search_job,
    enqueue_search_job,
    queue_position,
    search_jobs_grouped_by_user,
)
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


def test_top20_default_sources():
    assert len(DEFAULT_ENABLED_SOURCE_SLUGS) == 20
    assert "openalex" in DEFAULT_ENABLED_SOURCE_SLUGS
    assert "datacite" not in DEFAULT_ENABLED_SOURCE_SLUGS

"""Tests for batch existing-paper lookup and library facet caching."""

from __future__ import annotations

from app.database.connection import session_scope
from app.database.repository import (
    filter_new_paper_records,
    invalidate_library_facets_cache,
    library_facets,
    save_paper,
)
from app.models.paper import PaperRecord, PaperStatus


def test_filter_new_paper_records_bulk(tmp_db):
    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(title="Already here", doi="10.1000/exists", status=PaperStatus.FOUND),
        )
        save_paper(
            session,
            PaperRecord(title="Also here", doi="10.1000/exists-2", status=PaperStatus.OA_AVAILABLE),
        )
        fresh = filter_new_paper_records(
            session,
            [
                PaperRecord(title="Already here", doi="10.1000/exists", status=PaperStatus.FOUND),
                PaperRecord(title="Brand new", doi="10.1000/new", status=PaperStatus.FOUND),
                PaperRecord(title="Also here", doi="10.1000/exists-2", status=PaperStatus.FOUND),
            ],
        )
    assert [p.doi for p in fresh] == ["10.1000/new"]


def test_library_facets_are_cached(tmp_db):
    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Facet paper",
                doi="10.1000/facet",
                research_fields=["Cybersecurity"],
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
        invalidate_library_facets_cache()
        first = library_facets(session)
        second = library_facets(session)
    assert first["visible_total"] == second["visible_total"] == 1
    assert first["categories"][0]["name"] == "Cybersecurity"


def test_library_facets_light_skips_category_scan(tmp_db):
    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Light facet paper",
                doi="10.1000/light-facet",
                research_fields=["Cybersecurity"],
                publication_year=2024,
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
        invalidate_library_facets_cache()
        light = library_facets(session, light=True)
        full = library_facets(session, light=False)
    assert light["visible_total"] == full["visible_total"] == 1
    assert light["years"] == [2024]
    assert light["categories"] == []
    assert full["categories"][0]["name"] == "Cybersecurity"


def test_dashboard_stats_are_cached(tmp_db):
    from app.database.repository import dashboard_stats
    from fastapi.testclient import TestClient
    from app.web import app

    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Dash paper",
                doi="10.1000/dash",
                publication_year=2024,
                status=PaperStatus.OA_AVAILABLE,
                open_access=True,
            ),
        )
        invalidate_library_facets_cache()
        first = dashboard_stats(session)
        second = dashboard_stats(session)
    assert first["total"] == second["total"] == 1
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "Dash paper" in page.text or "Papers in library" in page.text

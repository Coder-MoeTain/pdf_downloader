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

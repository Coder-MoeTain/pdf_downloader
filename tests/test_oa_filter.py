from sqlalchemy import select

from app.database.connection import session_scope
from app.database.models import Paper, SearchResult
from app.database.repository import attach_search_result, create_search_query, save_paper
from app.models.paper import PaperRecord, PaperStatus
from app.models.search import SearchFilters
from app.services.oa_status import is_confirmed_oa
from app.services.search_service import SearchService


def test_doi_without_oa_copy_is_not_paywalled():
    paper = PaperRecord(title="Closed journal", doi="10.1000/closed", status=PaperStatus.NO_OA_COPY_FOUND)
    paper.open_access = False
    assert paper.status != PaperStatus.PAYWALLED
    assert is_confirmed_oa(paper) is False


def test_failed_unpaywall_is_unknown():
    paper = PaperRecord(title="Lookup failed", doi="10.1000/unknown", status=PaperStatus.OA_UNKNOWN)
    assert is_confirmed_oa(paper) is False


def test_oa_only_search_persists_only_confirmed_oa(tmp_db):
    oa = PaperRecord(
        title="OA paper",
        doi="10.1000/oa-keep",
        pdf_url="https://arxiv.org/pdf/1.pdf",
        status=PaperStatus.OA_AVAILABLE,
        open_access=True,
    )
    closed = PaperRecord(title="Closed paper", doi="10.1000/closed-drop", status=PaperStatus.NO_OA_COPY_FOUND)
    unknown = PaperRecord(title="Unknown paper", doi="10.1000/unknown-drop", status=PaperStatus.OA_UNKNOWN)
    unique = [oa, closed, unknown]
    kept = [paper for paper in unique if is_confirmed_oa(paper)]
    assert [paper.title for paper in kept] == ["OA paper"]
    filters = SearchFilters(query="oa only", open_access_only=True, download=False)
    with session_scope() as session:
        search = create_search_query(session, filters.query, [filters.query], {"open_access_only": True})
        for rank, paper in enumerate(kept, start=1):
            db_paper = save_paper(session, paper)
            attach_search_result(session, search.id, db_paper.id, rank, 1.0)
        save_paper(session, closed)
        save_paper(session, unknown)
        result_titles = list(
            session.scalars(select(Paper.title).join(SearchResult, SearchResult.paper_id == Paper.id)).all()
        )
    assert result_titles == ["OA paper"]


def test_oa_only_filter_applied_in_search_service(tmp_db, monkeypatch):
    async def fake_search(self, providers, query, filters, stats):
        return [
            PaperRecord(title="OA paper", doi="10.1000/oa-svc", pdf_url="https://arxiv.org/pdf/1.pdf"),
            PaperRecord(title="Closed paper", doi="10.1000/closed-svc"),
            PaperRecord(title="Unknown paper", doi="10.1000/unknown-svc"),
        ]

    async def fake_oa(self, oa, unique):
        for paper in unique:
            if paper.title.startswith("OA"):
                paper.status = PaperStatus.OA_AVAILABLE
                paper.open_access = True
                paper.pdf_url = "https://arxiv.org/pdf/1.pdf"
            elif paper.title.startswith("Closed"):
                paper.status = PaperStatus.NO_OA_COPY_FOUND
                paper.open_access = False
            else:
                paper.status = PaperStatus.OA_UNKNOWN

    monkeypatch.setattr(SearchService, "_search_providers", fake_search)
    monkeypatch.setattr(SearchService, "_resolve_open_access", fake_oa)
    monkeypatch.setattr("app.services.search_service.build_providers", lambda *args, **kwargs: [object()])
    service = SearchService()
    import asyncio

    stats = asyncio.run(
        service.run(SearchFilters(query="topic", open_access_only=True, download=False, max_results=10))
    )
    assert stats.unique_papers == 1
    with session_scope() as session:
        titles = list(session.scalars(select(Paper.title).join(SearchResult)).all())
    assert titles == ["OA paper"]

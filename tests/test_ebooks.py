from app.database.ebook_sources import EBOOK_SOURCE_SLUGS, EBOOK_SOURCES
from app.models.paper import PaperRecord, PaperStatus
from app.providers import PROVIDER_CLASSES
from app.providers.ebook_providers import EBOOK_PROVIDER_CLASSES, _InternetArchiveBooksProvider, _stamp_ebook


def test_fifty_ebook_sources_are_registered():
    slugs = {str(item["slug"]) for item in EBOOK_SOURCES}
    names = {cls.name for cls in PROVIDER_CLASSES}
    ebook_names = {cls.name for cls in EBOOK_PROVIDER_CLASSES}
    assert len(EBOOK_SOURCES) == 50
    assert slugs == EBOOK_SOURCE_SLUGS
    assert slugs <= names
    assert ebook_names == slugs
    families = {str(item["family"]) for item in EBOOK_SOURCES}
    assert families == {"science", "technology"}
    categories = {str(item["category"]) for item in EBOOK_SOURCES}
    assert "Data Science" in categories
    assert "Satellite Technology" in categories
    assert "Electronics" in categories
    assert "nasa_ntrs_eo_books" not in slugs
    assert "nasa_ntrs_tech_books" not in slugs
    assert all(str(item.get("backend") or "") != "nasa_ntrs" for item in EBOOK_SOURCES)


def test_catalog_includes_ebooks():
    from app.database.batch_sources import BATCH_CROSSREF_SOURCES
    from app.database.source_catalog import BUILTIN_SOURCES

    catalog = {str(item["slug"]) for item in BUILTIN_SOURCES}
    names = {cls.name for cls in PROVIDER_CLASSES}
    assert len(catalog) == 150
    assert catalog == names
    assert {str(item["slug"]) for item in BATCH_CROSSREF_SOURCES} <= catalog
    assert EBOOK_SOURCE_SLUGS <= catalog


def test_stamp_ebook_sets_category():
    paper = PaperRecord(title="Intro to SAR", work_type="article")
    stamped = _stamp_ebook(paper, {"category": "Satellite Technology", "family": "science"})
    assert stamped is not None
    assert stamped.work_type == "ebook"
    assert stamped.category == "Satellite Technology"
    assert "Satellite Technology" in stamped.research_fields


def test_internet_archive_parse():
    provider = _InternetArchiveBooksProvider()  # type: ignore[call-arg]
    provider.name = "ia_electronics"
    paper = provider._parse(
        {
            "identifier": "electronics-101",
            "title": "Electronics 101",
            "creator": "Ada Lovelace",
            "year": "2020",
            "description": "An open textbook.",
        }
    )
    assert paper is not None
    assert paper.work_type == "ebook"
    assert paper.cover_url == "https://archive.org/services/img/electronics-101"
    assert paper.extra["archive_id"] == "electronics-101"


def test_internet_archive_skips_journal_papers():
    from app.providers.ebook_providers import _looks_like_research_paper

    provider = _InternetArchiveBooksProvider()  # type: ignore[call-arg]
    provider.name = "ia_science_texts"
    paper = provider._parse(
        {
            "identifier": "arxiv-paper",
            "title": "Proceedings of the IEEE on remote sensing",
            "creator": "Ada Lovelace",
            "year": "2020",
            "description": "A conference paper.",
        }
    )
    assert paper is None
    assert _looks_like_research_paper("Journal of Geophysical Research", "")
    assert _looks_like_research_paper("DTIC ADA590405: Capability Set 13", "")
    assert _looks_like_research_paper("A LES-5 BEACON RECEIVER", "", "Defense Technical Information Center")
    assert _looks_like_research_paper("NASA Technical Reports Server (NTRS) study", "")
    assert not _looks_like_research_paper("Introduction to Remote Sensing", "An open textbook.")


def test_library_splits_papers_and_ebooks(tmp_db):
    from sqlalchemy import select

    from app.database.connection import session_scope
    from app.database.models import Paper
    from app.database.repository import query_library, save_paper

    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(title="A journal article", doi="10.1000/article-split", status=PaperStatus.FOUND, work_type="article"),
        )
        save_paper(
            session,
            PaperRecord(
                title="A data science ebook",
                doi="10.1000/ebook-split",
                status=PaperStatus.OA_AVAILABLE,
                work_type="ebook",
                category="Data Science",
                pdf_url="https://example.org/ebook-split.pdf",
                source_provider="doab_data_science",
                authors=[],
            ),
        )
        save_paper(
            session,
            PaperRecord(
                title="Ebook without a PDF",
                doi="10.1000/ebook-nopdf-split",
                status=PaperStatus.OA_AVAILABLE,
                work_type="ebook",
                category="Data Science",
                source_provider="doab_data_science",
            ),
        )
        papers, paper_total = query_library(session, work_type="article")
        ebooks, ebook_total = query_library(session, work_type="ebook")
        assert paper_total == 1
        assert ebook_total == 1
        assert papers[0].title == "A journal article"
        assert ebooks[0].title == "A data science ebook"
        assert ebooks[0].category == "Data Science"
        stored = session.scalar(select(Paper).where(Paper.doi == "10.1000/ebook-split"))
        assert stored is not None
        assert stored.work_type == "ebook"


def test_library_ebooks_page(tmp_db):
    from fastapi.testclient import TestClient

    from app.database.connection import session_scope
    from app.database.repository import save_paper
    from app.web import app
    from tests.conftest import login_admin

    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Satellite Systems Handbook",
                doi="10.1000/ebook-ui",
                status=PaperStatus.OA_AVAILABLE,
                work_type="ebook",
                category="Satellite Technology",
                publication_year=2024,
                cover_url="https://example.org/cover.png",
                pdf_url="https://example.org/satellite-handbook.pdf",
                source_provider="doab_remote_sensing",
            ),
        )
        save_paper(
            session,
            PaperRecord(
                title="Metadata only textbook",
                doi="10.1000/ebook-nopdf",
                status=PaperStatus.OA_AVAILABLE,
                work_type="ebook",
                category="Data Science",
                source_provider="doab_data_science",
            ),
        )
    client = login_admin(TestClient(app))
    papers_page = client.get("/library")
    ebooks_page = client.get("/library?kind=ebooks")
    assert papers_page.status_code == 200
    assert "Research papers" in papers_page.text
    assert "Satellite Systems Handbook" not in papers_page.text
    assert ebooks_page.status_code == 200
    assert "Satellite Systems Handbook" in ebooks_page.text
    assert "Metadata only textbook" not in ebooks_page.text
    assert "Satellite Technology" in ebooks_page.text
    assert 'href="/library?kind=ebooks"' in papers_page.text


def test_library_ebooks_page_hides_research_papers(tmp_db):
    from fastapi.testclient import TestClient

    from app.database.connection import session_scope
    from app.database.repository import reclassify_non_ebook_records, save_paper
    from app.web import app
    from tests.conftest import login_admin

    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Open Remote Sensing Handbook",
                doi="10.1000/ebook-real",
                status=PaperStatus.OA_AVAILABLE,
                work_type="ebook",
                category="Satellite Technology",
                pdf_url="https://example.org/handbook.pdf",
                source_provider="doab_remote_sensing",
            ),
        )
        save_paper(
            session,
            PaperRecord(
                title="A journal article tagged as an ebook",
                doi="10.1000/paper-as-ebook",
                status=PaperStatus.OA_AVAILABLE,
                work_type="ebook",
                category="Research Papers",
                pdf_url="https://example.org/paper.pdf",
                source_provider="arxiv",
            ),
        )
        save_paper(
            session,
            PaperRecord(
                title="NASA technical report stamped ebook",
                doi="10.1000/ntrs-as-ebook",
                status=PaperStatus.OA_AVAILABLE,
                work_type="ebook",
                pdf_url="https://example.org/ntrs.pdf",
                source_provider="nasa_ntrs_eo_books",
            ),
        )
        save_paper(
            session,
            PaperRecord(
                title="DTIC ADA590405: Capability Set 13",
                doi="10.1000/dtic-as-ebook",
                status=PaperStatus.OA_AVAILABLE,
                work_type="ebook",
                pdf_url="https://example.org/dtic.pdf",
                source_provider="ia_aerospace",
                publisher="Defense Technical Information Center",
            ),
        )
        moved = reclassify_non_ebook_records(session)
        assert moved == 3

    client = login_admin(TestClient(app))
    ebooks_page = client.get("/library?kind=ebooks")
    papers_page = client.get("/library")
    assert ebooks_page.status_code == 200
    assert "Open Remote Sensing Handbook" in ebooks_page.text
    assert "A journal article tagged as an ebook" not in ebooks_page.text
    assert "NASA technical report stamped ebook" not in ebooks_page.text
    assert "DTIC ADA590405" not in ebooks_page.text
    assert "Re-check Unpaywall" not in ebooks_page.text
    assert 'name="kind" value="ebooks"' in ebooks_page.text
    assert papers_page.status_code == 200
    assert "A journal article tagged as an ebook" in papers_page.text
    assert "NASA technical report stamped ebook" in papers_page.text
    assert "DTIC ADA590405" in papers_page.text
    assert "Open Remote Sensing Handbook" not in papers_page.text
    assert "Re-check Unpaywall" in papers_page.text


def test_search_filters_ebook_providers():
    from types import SimpleNamespace

    from app.providers.base import ResearchProvider
    from app.providers.ebook_providers import _OpenAlexBookProvider
    from app.services.search_service import filters_from_cli, select_search_providers

    filters = filters_from_cli("machine learning", collection="ebooks", ebook_category="Data Science", no_download=True)
    assert filters.collection == "ebooks"
    assert filters.ebook_category == "Data Science"
    classes = [cls for cls in PROVIDER_CLASSES if getattr(cls, "content_kind", "article") == "ebook"]
    assert classes
    assert all(issubclass(cls, ResearchProvider) for cls in classes)
    matching = [cls for cls in classes if getattr(cls, "category", "") == "Data Science"]
    assert matching

    providers = [
        SimpleNamespace(name="openalex", content_kind="article", category=""),
        SimpleNamespace(name="doab_ds", content_kind="ebook", category="Data Science"),
        SimpleNamespace(name="ia_electronics", content_kind="ebook", category="Electronics"),
    ]
    ebook_filters = filters_from_cli("radar", collection="ebooks", source="openalex", no_download=True)
    assert select_search_providers(providers, ebook_filters) == []
    paper_filters = filters_from_cli("radar", collection="papers", source="doab_ds", no_download=True)
    assert select_search_providers(providers, paper_filters) == []
    ds_filters = filters_from_cli("radar", collection="ebooks", source="doab_ds", no_download=True)
    assert [p.name for p in select_search_providers(providers, ds_filters)] == ["doab_ds"]

    oa_filters = filters_from_cli("radar", collection="ebooks", no_download=True)
    book_provider = _OpenAlexBookProvider()
    params = book_provider._search_params(oa_filters.query, oa_filters)
    assert "type:book" in str(params.get("filter") or "")
    assert "type:article" not in str(params.get("filter") or "")


def test_settings_splits_academic_and_ebook_source_pages(tmp_db):
    from fastapi.testclient import TestClient

    from app.web import app
    from tests.conftest import login_admin

    client = login_admin(TestClient(app))
    academic = client.get("/settings?section=sources")
    ebooks = client.get("/settings?section=sources&kind=ebooks")
    alias = client.get("/settings?section=ebook_sources")
    assert academic.status_code == 200
    assert ebooks.status_code == 200
    assert alias.status_code == 200
    assert "Academic sources" in academic.text
    assert "Ebook sources" in academic.text
    assert "<strong>OpenAlex</strong>" in academic.text
    assert "DOAB · Data Science" not in academic.text
    assert "All kinds" not in academic.text
    assert "DOAB · Data Science" in ebooks.text
    assert "Satellite Technology" in ebooks.text
    assert 'href="/settings?section=sources&amp;kind=ebooks"' in academic.text
    assert "DOAB · Data Science" in alias.text
    assert "<strong>OpenAlex</strong>" not in alias.text


def test_search_pages_split_papers_and_ebooks(tmp_db):
    from fastapi.testclient import TestClient

    from app.web import app
    from tests.conftest import login_admin

    client = login_admin(TestClient(app))
    papers = client.get("/search")
    ebooks = client.get("/search?collection=ebooks")
    assert papers.status_code == 200
    assert ebooks.status_code == 200
    assert "Search papers" in papers.text
    assert "Research papers" in papers.text
    assert 'value="papers"' in papers.text
    assert "Ebook subject" not in papers.text
    assert "DOAB · Data Science" not in papers.text
    assert "Search ebooks" in ebooks.text
    assert "Ebook subject" in ebooks.text
    assert 'value="ebooks"' in ebooks.text
    assert "DOAB · Data Science" in ebooks.text
    assert "<strong>OpenAlex</strong>" not in ebooks.text
    assert "OpenAlex Books" in ebooks.text


def test_ebooks_latest_results_exclude_research_papers(tmp_db):
    from fastapi.testclient import TestClient

    from app.database.connection import session_scope
    from app.database.repository import attach_search_result, create_search_query, save_paper
    from app.web import app
    from tests.conftest import login_admin

    with session_scope() as session:
        article = save_paper(
            session,
            PaperRecord(
                title="A journal article in latest search",
                doi="10.1000/latest-article",
                status=PaperStatus.OA_AVAILABLE,
                work_type="article",
                pdf_url="https://example.org/article.pdf",
            ),
        )
        ebook = save_paper(
            session,
            PaperRecord(
                title="An ebook in latest search",
                doi="10.1000/latest-ebook",
                status=PaperStatus.OA_AVAILABLE,
                work_type="ebook",
                category="Electronics",
                pdf_url="https://example.org/ebook.pdf",
                source_provider="doab_electronics",
            ),
        )
        search = create_search_query(session, "radar", ["radar"], {"collection": "ebooks"})
        attach_search_result(session, search.id, article.id, 1, 1.0)
        attach_search_result(session, search.id, ebook.id, 2, 0.9)

    client = login_admin(TestClient(app))
    ebooks_latest = client.get("/library?latest=1&kind=ebooks")
    papers_latest = client.get("/library?latest=1")
    assert ebooks_latest.status_code == 200
    assert "An ebook in latest search" in ebooks_latest.text
    assert "A journal article in latest search" not in ebooks_latest.text
    assert papers_latest.status_code == 200
    assert "A journal article in latest search" in papers_latest.text
    assert "An ebook in latest search" not in papers_latest.text


def test_openalex_book_provider_skips_articles():
    from app.providers.ebook_providers import _openalex_is_book

    assert _openalex_is_book({"type": "book", "display_name": "A textbook"})
    assert _openalex_is_book({"type": "monograph", "display_name": "A monograph"})
    assert not _openalex_is_book({"type": "book-chapter", "display_name": "A chapter"})
    assert not _openalex_is_book({"type": "article", "display_name": "A journal paper"})
    assert not _openalex_is_book({"type": "journal-article", "display_name": "A journal paper"})

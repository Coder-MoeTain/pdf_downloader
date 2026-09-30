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
                status=PaperStatus.FOUND,
                work_type="ebook",
                category="Data Science",
                authors=[],
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
                status=PaperStatus.FOUND,
                work_type="ebook",
                category="Satellite Technology",
                publication_year=2024,
                cover_url="https://example.org/cover.png",
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
    assert "Satellite Technology" in ebooks_page.text
    assert 'href="/library?kind=ebooks"' in papers_page.text


def test_search_filters_ebook_providers():
    from app.providers.base import ResearchProvider
    from app.services.search_service import filters_from_cli

    filters = filters_from_cli("machine learning", collection="ebooks", ebook_category="Data Science", no_download=True)
    assert filters.collection == "ebooks"
    assert filters.ebook_category == "Data Science"
    classes = [cls for cls in PROVIDER_CLASSES if getattr(cls, "content_kind", "article") == "ebook"]
    assert classes
    assert all(issubclass(cls, ResearchProvider) for cls in classes)
    matching = [cls for cls in classes if getattr(cls, "category", "") == "Data Science"]
    assert matching


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

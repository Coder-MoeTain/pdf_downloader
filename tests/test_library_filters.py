from fastapi.testclient import TestClient

from app.database.connection import session_scope
from app.database.models import Paper
from app.database.repository import apply_paper_filters, save_paper, set_paper_rating
from app.models.paper import PaperRecord, PaperStatus
from app.web import app
from sqlalchemy import select


def test_user_rating_set_and_clear(tmp_db):
    record = PaperRecord(title="Rated paper", doi="10.1000/rate-me", status=PaperStatus.FOUND)
    with session_scope() as session:
        paper = save_paper(session, record)
        paper_id = paper.id
        set_paper_rating(session, paper_id, 5)
    with session_scope() as session:
        paper = session.get(Paper, paper_id)
        assert paper.user_rating == 5
        set_paper_rating(session, paper_id, 0)
    with session_scope() as session:
        paper = session.get(Paper, paper_id)
        assert paper.user_rating is None


def test_user_rating_survives_metadata_update(tmp_db):
    record = PaperRecord(title="Keep stars", doi="10.1000/keep-stars", status=PaperStatus.FOUND)
    with session_scope() as session:
        paper = save_paper(session, record)
        set_paper_rating(session, paper.id, 4)
        paper_id = paper.id
    updated = PaperRecord(title="Keep stars", doi="10.1000/keep-stars", status=PaperStatus.OA_AVAILABLE)
    with session_scope() as session:
        save_paper(session, updated)
        paper = session.get(Paper, paper_id)
        assert paper.user_rating == 4
        assert paper.status == "OA_AVAILABLE"


def test_downloadable_filter_excludes_paywalled(tmp_db):
    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Open PDF",
                doi="10.1000/oa",
                pdf_url="https://arxiv.org/pdf/1234.5678.pdf",
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
        save_paper(
            session,
            PaperRecord(title="Closed", doi="10.1000/closed", status=PaperStatus.PAYWALLED),
        )
        save_paper(
            session,
            PaperRecord(title="No file", doi="10.1000/none", status=PaperStatus.NO_PDF),
        )
        stmt = apply_paper_filters(select(Paper), downloadable=True)
        titles = {p.title for p in session.scalars(stmt)}
        assert titles == {"Open PDF"}


def test_min_rating_filter(tmp_db):
    with session_scope() as session:
        low = save_paper(session, PaperRecord(title="Low", doi="10.1000/low", status=PaperStatus.FOUND))
        high = save_paper(session, PaperRecord(title="High", doi="10.1000/high", status=PaperStatus.FOUND))
        set_paper_rating(session, low.id, 2)
        set_paper_rating(session, high.id, 5)
        titles = {p.title for p in session.scalars(apply_paper_filters(select(Paper), min_rating=4))}
        assert titles == {"High"}


def test_rating_api_and_downloadable_page(tmp_db):
    with session_scope() as session:
        paper = save_paper(
            session,
            PaperRecord(
                title="OA paper for dashboard",
                doi="10.1000/api-rate",
                pdf_url="https://arxiv.org/pdf/1.pdf",
                status=PaperStatus.OA_AVAILABLE,
            ),
        )
        paper_id = paper.id
    client = TestClient(app)
    response = client.post(f"/api/papers/{paper_id}/rating", json={"rating": 5})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "rating": 5}
    page = client.get("/library?pdf=1")
    assert page.status_code == 200
    assert "OA paper for dashboard" in page.text
    assert "downloadable pdf" in page.text.lower()
    rated = client.get("/library?min_rating=4")
    assert rated.status_code == 200
    assert "OA paper for dashboard" in rated.text
    empty = client.get("/library?min_rating=5&status=PAYWALLED")
    assert empty.status_code == 200
    assert "Nothing matches this filter" in empty.text


def test_hide_paywalled_filters_library_unless_explicit(tmp_db):
    from app.database.repository import upsert_download
    from app.database.settings_repository import save_workspace_settings

    with session_scope() as session:
        open_paper = save_paper(
            session,
            PaperRecord(title="Open visible paper", doi="10.1000/open-visible", status=PaperStatus.OA_AVAILABLE),
        )
        closed = save_paper(
            session,
            PaperRecord(title="Closed paywalled paper", doi="10.1000/closed-hidden", status=PaperStatus.PAYWALLED),
        )
        upsert_download(session, open_paper.id, pdf_url="https://arxiv.org/pdf/1.pdf", status=PaperStatus.OA_AVAILABLE.value)
        upsert_download(session, closed.id, pdf_url=None, status=PaperStatus.PAYWALLED.value)
    save_workspace_settings(
        {
            "contact_email": "lab@university.edu",
            "library_dir": "research_library",
            "timezone": "UTC",
            "check_robots_txt": True,
            "prefer_https": True,
            "show_paywalled": False,
        }
    )
    client = TestClient(app)
    library = client.get("/library")
    assert library.status_code == 200
    assert "Open visible paper" in library.text
    assert "Closed paywalled paper" not in library.text
    assert "Paywalled papers are hidden" in library.text
    explicit = client.get("/library?status=PAYWALLED")
    assert explicit.status_code == 200
    assert "Closed paywalled paper" in explicit.text
    downloads = client.get("/downloads")
    assert downloads.status_code == 200
    assert "Open visible paper" in downloads.text
    assert "Closed paywalled paper" not in downloads.text
    assert "Paywalled papers are hidden" in downloads.text
    assert "/downloads?status=PAYWALLED" not in downloads.text
    paywalled_downloads = client.get("/downloads?status=PAYWALLED")
    assert paywalled_downloads.status_code == 200
    assert "Closed paywalled paper" in paywalled_downloads.text
    home = client.get("/")
    assert home.status_code == 200
    settings = client.get("/settings?section=workspace")
    assert settings.status_code == 200
    assert "Show paywalled papers" in settings.text
    assert 'name="show_paywalled"' in settings.text

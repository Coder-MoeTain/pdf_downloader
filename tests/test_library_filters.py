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

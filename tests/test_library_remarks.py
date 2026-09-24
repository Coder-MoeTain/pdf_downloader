"""Library remark button / modal wiring."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.database.connection import session_scope
from app.database.repository import save_paper
from app.models.paper import PaperRecord, PaperStatus
from app.web import app
from tests.conftest import login_admin


def test_library_remark_button_and_save(tmp_db):
    with session_scope() as session:
        paper = save_paper(
            session,
            PaperRecord(title="Remark UI paper", doi="10.1000/remark-ui", status=PaperStatus.FOUND),
        )
        paper_id = paper.id

    client = login_admin(TestClient(app))
    page = client.get("/library")
    assert page.status_code == 200
    html = page.text
    assert 'id="paperRemarkModal"' in html
    assert 'id="paperRemarkForm"' in html
    assert "remark-btn" in html
    assert f'data-remark-paper-id="{paper_id}"' in html

    save = client.post(
        f"/papers/{paper_id}/notes",
        data={"notes": "Saved from remark modal", "next": "/library"},
        follow_redirects=False,
    )
    assert save.status_code == 303

    workspace = client.get(f"/api/papers/{paper_id}/workspace")
    assert workspace.status_code == 200
    assert workspace.json()["notes"] == "Saved from remark modal"

from app.auth import create_local_user
from app.database.connection import session_scope
from app.database.repository import (
    add_paper_to_collection,
    ensure_default_collections,
    save_paper,
    set_paper_rating,
    set_user_paper_notes,
)
from app.models.paper import PaperRecord, PaperStatus


def test_user_notes_and_ratings_are_scoped(tmp_db):
    with session_scope() as session:
        paper = save_paper(session, PaperRecord(title="Private notes", doi="10.1000/notes", status=PaperStatus.FOUND))
        user_a = create_local_user(session, email="a@lab.test", password="password1", name="A", role="user")
        user_b = create_local_user(session, email="b@lab.test", password="password1", name="B", role="user")
        set_paper_rating(session, paper.id, 5, user_id=user_a.id)
        notes = set_user_paper_notes(session, user_id=user_a.id, paper_id=paper.id, notes="Need to cite")
        other = set_user_paper_notes(session, user_id=user_b.id, paper_id=paper.id, notes="Different user")
        assert notes.notes == "Need to cite"
        assert other.notes == "Different user"
        assert notes.id != other.id


def test_default_collections_and_saved_search(tmp_db):
    from app.auth import create_local_user

    with session_scope() as session:
        user = create_local_user(session, email="col@lab.test", password="password1", name="Col", role="user")
        paper = save_paper(session, PaperRecord(title="To collect", doi="10.1000/col", status=PaperStatus.FOUND))
        collections = ensure_default_collections(session, user_id=user.id)
        names = {row.name for row in collections}
        assert {"Reading List", "Literature Review", "Important", "To Cite"} <= names
        add_paper_to_collection(session, user_id=user.id, collection_id=collections[0].id, paper_id=paper.id)


def test_ordinary_user_cannot_access_admin_settings(tmp_db):
    from fastapi.testclient import TestClient

    from app.auth import create_local_user, invalidate_user_count_cache
    from app.database.connection import session_scope
    from app.web import app

    with session_scope() as session:
        create_local_user(session, email="reader@lab.test", password="ReaderPass1!", name="Reader", role="user")
    invalidate_user_count_cache()
    client = TestClient(app, follow_redirects=False)
    client.get("/login")
    client.post("/login", data={"email": "reader@lab.test", "password": "ReaderPass1!", "next": "/"})
    denied = client.get("/settings")
    assert denied.status_code in {302, 403}


def test_workspace_and_saved_search_routes(tmp_db):
    from fastapi.testclient import TestClient

    from app.database.connection import session_scope
    from app.database.repository import save_paper
    from app.models.paper import PaperRecord, PaperStatus
    from app.web import app
    from tests.conftest import login_admin

    with session_scope() as session:
        paper = save_paper(session, PaperRecord(title="Workspace paper", doi="10.1000/ws", status=PaperStatus.FOUND))
        paper_id = paper.id
    client = login_admin(TestClient(app))
    workspace = client.get(f"/api/papers/{paper_id}/workspace")
    assert workspace.status_code == 200
    payload = workspace.json()
    assert payload["ok"] is True
    assert payload["reading_status"] == "unread"
    notes = client.post(
        f"/papers/{paper_id}/notes",
        data={"notes": "Cite this", "tags": "ai; security", "next": "/library"},
        follow_redirects=False,
    )
    assert notes.status_code == 303
    saved = client.post(
        "/search/save",
        data={"query": "federated learning", "name": "Weekly FL"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    search_page = client.get("/search")
    assert "Weekly FL" in search_page.text
    audit = client.get("/settings?section=audit")
    assert audit.status_code == 200
    assert "Security audit log" in audit.text

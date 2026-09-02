from sqlalchemy import select

from app.database.connection import session_scope
from app.database.models import Paper
from app.database.repository import save_paper
from app.models.paper import AuthorRecord, PaperRecord, PaperStatus


def test_save_and_find_by_doi(tmp_db):
    record = PaperRecord(
        title="Test Paper",
        doi="10.1000/save-me",
        authors=[AuthorRecord(name="Grace Hopper")],
        publication_year=2024,
        status=PaperStatus.FOUND,
        source_provider="crossref",
    )
    with session_scope() as session:
        paper = save_paper(session, record)
        assert paper.id is not None
        found = session.scalar(select(Paper).where(Paper.doi == "10.1000/save-me"))
        assert found is not None
        assert found.title == "Test Paper"
        assert found.normalized_title
        assert found.authors

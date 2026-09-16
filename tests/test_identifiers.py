from sqlalchemy import select

from app.database.connection import session_scope
from app.database.identifiers import normalize_identifier
from app.database.models import Paper, PaperIdentifier
from app.database.repository import get_or_create_author, save_paper
from app.models.paper import AuthorRecord, PaperRecord, PaperStatus


def test_doi_normalization_strips_resolver_prefix():
    assert normalize_identifier("doi", "https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert normalize_identifier("doi", "DOI: 10.1000/ABC") == "10.1000/abc"


def test_identifier_unique_constraint_merges_same_doi(tmp_db):
    first = PaperRecord(title="Canonical", doi="https://doi.org/10.1000/Same-Id", status=PaperStatus.FOUND)
    second = PaperRecord(title="Duplicate insert", doi="10.1000/same-id", status=PaperStatus.OA_AVAILABLE)
    with session_scope() as session:
        a = save_paper(session, first)
        b = save_paper(session, second)
        assert a.id == b.id
        papers = session.scalars(select(Paper)).all()
        assert len(papers) == 1
        ids = session.scalars(select(PaperIdentifier)).all()
        assert len(ids) == 1
        assert ids[0].normalized_value == "10.1000/same-id"


def test_identifier_conflict_merges_orphan_row(tmp_db):
    from app.database.repository import _sync_paper_identifiers

    with session_scope() as session:
        canonical = save_paper(
            session,
            PaperRecord(title="Canonical owner", doi="10.1000/race-doi", status=PaperStatus.FOUND),
        )
        orphan = Paper(title="Orphan duplicate", normalized_title="orphan duplicate", status="FOUND")
        session.add(orphan)
        session.flush()
        orphan_id = orphan.id
        winner = _sync_paper_identifiers(
            session,
            orphan,
            PaperRecord(title="Orphan duplicate", doi="10.1000/race-doi", status=PaperStatus.OA_AVAILABLE),
        )
        assert winner.id == canonical.id
        assert session.get(Paper, orphan_id) is None
        papers = session.scalars(select(Paper)).all()
        assert len(papers) == 1
        ids = session.scalars(select(PaperIdentifier)).all()
        assert len(ids) == 1
        assert ids[0].paper_id == canonical.id


def test_authors_with_orcid_are_not_merged_by_name_alone(tmp_db):
    with session_scope() as session:
        one = get_or_create_author(session, AuthorRecord(name="Jane Smith", orcid="0000-0001-1111-1111"))
        two = get_or_create_author(session, AuthorRecord(name="Jane Smith", orcid="0000-0002-2222-2222"))
        assert one.id != two.id

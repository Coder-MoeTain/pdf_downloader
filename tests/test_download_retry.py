from app.database.connection import session_scope
from app.database.repository import upsert_download
from app.database.models import Paper
from app.models.paper import PaperStatus
from sqlalchemy import select


def test_download_retry_count(tmp_db):
    with session_scope() as session:
        paper = Paper(title="Retry me", normalized_title="retry me", status="OA_AVAILABLE")
        session.add(paper)
        session.flush()
        row = upsert_download(session, paper.id, pdf_url="https://arxiv.org/pdf/x.pdf", status=PaperStatus.FAILED.value, error="timeout", increment_retry=True)
        assert row.retry_count == 1
        row = upsert_download(session, paper.id, pdf_url="https://arxiv.org/pdf/x.pdf", status=PaperStatus.FAILED.value, error="timeout", increment_retry=True)
        assert row.retry_count == 2
        row = upsert_download(session, paper.id, pdf_url="https://arxiv.org/pdf/x.pdf", status=PaperStatus.DOWNLOADED.value, local_path="x.pdf", sha256="abc")
        assert row.status == "DOWNLOADED"
        assert row.sha256 == "abc"


def test_upsert_download_records_user_once(tmp_db):
    from app.auth import create_local_user
    from app.database.models import Download

    with session_scope() as session:
        alice = create_local_user(session, email="alice@lab.edu", password="password1", name="Alice")
        bob = create_local_user(session, email="bob@lab.edu", password="password1", name="Bob")
        paper = Paper(title="Who downloaded", normalized_title="who downloaded", status="OA_AVAILABLE")
        session.add(paper)
        session.flush()
        row = upsert_download(
            session,
            paper.id,
            pdf_url="https://arxiv.org/pdf/x.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path="x.pdf",
            user_id=alice.id,
        )
        assert row.downloaded_by_user_id == alice.id
        row = upsert_download(
            session,
            paper.id,
            pdf_url="https://arxiv.org/pdf/x.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path="x.pdf",
            user_id=bob.id,
        )
        assert row.downloaded_by_user_id == alice.id
        stored = session.scalar(select(Download).where(Download.paper_id == paper.id))
        assert stored.downloaded_by_user_id == alice.id


from app.database.connection import session_scope
from app.database.repository import upsert_download
from app.database.models import Paper
from app.models.paper import PaperStatus


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

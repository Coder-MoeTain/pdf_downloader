import hashlib

import pytest

from app.config import get_runtime_config
from app.database.connection import session_scope
from app.database.models import Download, Paper
from app.database.repository import save_paper, upsert_download
from app.models.paper import PaperRecord, PaperStatus
from app.services.download_service import DownloadService
from sqlalchemy import select
from sqlalchemy.orm import selectinload


PDF_BYTES = b"%PDF-1.7\n" + (b"duplicate-pdf-body" * 200)


@pytest.mark.asyncio
async def test_duplicate_pdf_is_not_stored_twice(tmp_db, tmp_path, monkeypatch):
    digest = hashlib.sha256(PDF_BYTES).hexdigest()
    cfg = get_runtime_config().model_copy(deep=True)
    cfg.library_dir = tmp_path / "library"
    service = DownloadService(client=object(), config=cfg)

    first = PaperRecord(
        title="Original paper",
        doi="10.1000/dup-a",
        pdf_url="https://arxiv.org/pdf/1111.1111.pdf",
        publication_year=2024,
        status=PaperStatus.OA_AVAILABLE,
    )
    second = PaperRecord(
        title="Same PDF later",
        doi="10.1000/dup-b",
        pdf_url="https://arxiv.org/pdf/2222.2222.pdf",
        publication_year=2024,
        status=PaperStatus.OA_AVAILABLE,
    )

    with session_scope() as session:
        paper_a = save_paper(session, first)
        dest_a = service.topic_dir("library", 2024)
        dest_a.mkdir(parents=True, exist_ok=True)
        path_a = dest_a / "original.pdf"
        path_a.write_bytes(PDF_BYTES)
        upsert_download(
            session,
            paper_a.id,
            pdf_url=first.pdf_url,
            status=PaperStatus.DOWNLOADED.value,
            local_path=str(path_a),
            file_size=len(PDF_BYTES),
            sha256=digest,
        )
        paper_a.status = PaperStatus.DOWNLOADED.value
        first_id = paper_a.id

        paper_b = save_paper(session, second)
        second_id = paper_b.id
        record_b = second

    async def fake_stream(self, url, dest, max_size, on_progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(PDF_BYTES)
        if on_progress:
            on_progress(len(PDF_BYTES), len(PDF_BYTES))
        return len(PDF_BYTES), digest

    monkeypatch.setattr(DownloadService, "_stream_pdf", fake_stream)
    monkeypatch.setattr("app.services.download_service.robots_allowed", lambda *_args, **_kwargs: True)

    updated = await service.download_paper(second_id, record_b, "library")
    assert updated.status == PaperStatus.DUPLICATE
    assert updated.extra["duplicate_of"] == first_id

    with session_scope() as session:
        paper_b = session.scalar(
            select(Paper).options(selectinload(Paper.downloads)).where(Paper.id == second_id)
        )
        download_b = session.scalar(select(Download).where(Download.paper_id == second_id))
        assert paper_b.status == PaperStatus.DUPLICATE.value
        assert download_b.status == PaperStatus.DUPLICATE.value
        assert download_b.local_path == str(path_a)
        assert download_b.sha256 == digest

    pdfs = list((tmp_path / "library").rglob("*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].resolve() == path_a.resolve()
    assert pdfs[0].read_bytes() == PDF_BYTES

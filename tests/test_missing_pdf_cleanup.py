"""Tests for missing on-disk PDF cleanup."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database.connection import session_scope
from app.database.models import Download, Paper
from app.database.repository import save_paper, upsert_download
from app.models.paper import PaperRecord, PaperStatus
from app.services.missing_pdf_cleanup import cleanup_missing_pdf_records, delete_papers_without_local_pdf
from app.web import app


def test_cleanup_removes_missing_download_keeps_existing(tmp_db, monkeypatch, tmp_path):
    from app.config import get_runtime_config

    cfg = get_runtime_config()
    library = tmp_path / "library"
    topic = library / "topic" / "2024"
    topic.mkdir(parents=True)
    good_pdf = topic / "good.pdf"
    good_pdf.write_bytes(b"%PDF-1.4 kept")
    missing_pdf = topic / "gone.pdf"

    monkeypatch.setattr(cfg, "library_dir", library)
    monkeypatch.setattr(cfg, "min_pdf_size_bytes", 1)

    with session_scope() as session:
        kept = save_paper(
            session,
            PaperRecord(
                title="Still on disk",
                doi="10.1000/kept",
                pdf_url="https://example.com/kept.pdf",
                status=PaperStatus.DOWNLOADED,
            ),
        )
        gone = save_paper(
            session,
            PaperRecord(
                title="File missing",
                doi="10.1000/gone",
                pdf_url="https://example.com/gone.pdf",
                status=PaperStatus.DOWNLOADED,
            ),
        )
        upsert_download(
            session,
            kept.id,
            pdf_url="https://example.com/kept.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path=str(good_pdf),
        )
        upsert_download(
            session,
            gone.id,
            pdf_url="https://example.com/gone.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path=str(missing_pdf),
        )
        kept_id, gone_id = kept.id, gone.id

    assert not missing_pdf.exists()
    stats = cleanup_missing_pdf_records(cfg)
    assert stats.downloads_cleared == 1
    assert stats.papers_updated == 1

    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Download)) == 1
        kept_paper = session.get(Paper, kept_id)
        gone_paper = session.get(Paper, gone_id)
        assert kept_paper is not None
        assert gone_paper is not None
        assert kept_paper.status == PaperStatus.DOWNLOADED.value
        assert gone_paper.status == PaperStatus.OA_AVAILABLE.value
        assert gone_paper.title == "File missing"


def test_settings_cleanup_missing_pdfs_endpoint(tmp_db, monkeypatch, tmp_path):
    from app.config import get_runtime_config

    cfg = get_runtime_config()
    library = tmp_path / "library"
    library.mkdir(parents=True)
    monkeypatch.setattr(cfg, "library_dir", library)
    monkeypatch.setattr(cfg, "min_pdf_size_bytes", 1)

    with session_scope() as session:
        paper = save_paper(
            session,
            PaperRecord(
                title="Ghost download",
                doi="10.1000/ghost",
                pdf_url="https://example.com/ghost.pdf",
                status=PaperStatus.DOWNLOADED,
            ),
        )
        upsert_download(
            session,
            paper.id,
            pdf_url="https://example.com/ghost.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path=str(library / "missing.pdf"),
        )

    client = TestClient(app)
    cancelled = client.post("/settings/cleanup-missing-pdfs", data={"confirm": "nope"}, follow_redirects=False)
    assert cancelled.status_code in {302, 303}
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Download)) == 1

    ok = client.post("/settings/cleanup-missing-pdfs", data={"confirm": "CLEANUP"}, follow_redirects=False)
    assert ok.status_code in {302, 303}
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Download)) == 0
        paper = session.scalar(select(Paper).where(Paper.doi == "10.1000/ghost"))
        assert paper is not None
        assert paper.status == PaperStatus.OA_AVAILABLE.value


def test_delete_papers_without_local_pdf_keeps_real_files(tmp_db, monkeypatch, tmp_path):
    from app.config import get_runtime_config

    cfg = get_runtime_config()
    library = tmp_path / "library"
    topic = library / "topic"
    topic.mkdir(parents=True)
    good_pdf = topic / "kept.pdf"
    good_pdf.write_bytes(b"%PDF-1.4 kept")

    monkeypatch.setattr(cfg, "library_dir", library)
    monkeypatch.setattr(cfg, "min_pdf_size_bytes", 1)

    with session_scope() as session:
        kept = save_paper(
            session,
            PaperRecord(
                title="Has PDF",
                doi="10.1000/has-pdf",
                pdf_url="https://example.com/kept.pdf",
                status=PaperStatus.DOWNLOADED,
            ),
        )
        meta_only = save_paper(
            session,
            PaperRecord(
                title="Metadata only",
                doi="10.1000/meta",
                status=PaperStatus.FOUND,
            ),
        )
        ghost = save_paper(
            session,
            PaperRecord(
                title="Ghost file",
                doi="10.1000/ghost-del",
                pdf_url="https://example.com/ghost.pdf",
                status=PaperStatus.DOWNLOADED,
            ),
        )
        upsert_download(
            session,
            kept.id,
            pdf_url="https://example.com/kept.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path=str(good_pdf),
        )
        upsert_download(
            session,
            ghost.id,
            pdf_url="https://example.com/ghost.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path=str(topic / "missing.pdf"),
        )
        kept_id, meta_id, ghost_id = kept.id, meta_only.id, ghost.id

    stats = delete_papers_without_local_pdf(cfg)
    assert stats.papers_deleted == 2

    with session_scope() as session:
        assert session.get(Paper, kept_id) is not None
        assert session.get(Paper, meta_id) is None
        assert session.get(Paper, ghost_id) is None
        assert session.scalar(select(func.count()).select_from(Paper)) == 1
        assert good_pdf.is_file()


def test_settings_delete_papers_without_pdf_endpoint(tmp_db, monkeypatch, tmp_path):
    from app.config import get_runtime_config

    cfg = get_runtime_config()
    library = tmp_path / "library"
    library.mkdir(parents=True)
    monkeypatch.setattr(cfg, "library_dir", library)
    monkeypatch.setattr(cfg, "min_pdf_size_bytes", 1)

    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(title="No file", doi="10.1000/nofile", status=PaperStatus.FOUND),
        )

    client = TestClient(app)
    cancelled = client.post(
        "/settings/delete-papers-without-pdf",
        data={"confirm": "nope"},
        follow_redirects=False,
    )
    assert cancelled.status_code in {302, 303}
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Paper)) == 1

    ok = client.post(
        "/settings/delete-papers-without-pdf",
        data={"confirm": "DELETE"},
        follow_redirects=False,
    )
    assert ok.status_code in {302, 303}
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Paper)) == 0

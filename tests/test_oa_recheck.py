"""Re-check Unpaywall for papers stored as paywalled after a contact email is set."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database.connection import session_scope
from app.database.models import Paper
from app.database.repository import save_paper
from app.models.paper import PaperRecord, PaperStatus
from app.services.download_service import DownloadError, ensure_local_pdf, recheck_paywalled_open_access
from app.services.progress import clear_download_batch_owner, download_tracker
from app.web import app
from tests.conftest import login_admin


@pytest.fixture(autouse=True)
def _reset_download_tracker():
    clear_download_batch_owner()
    download_tracker.reset()
    yield
    clear_download_batch_owner()
    download_tracker.reset()


@pytest.mark.asyncio
async def test_recheck_downloads_when_unpaywall_finds_pdf(tmp_db, monkeypatch):
    with session_scope() as session:
        paper = save_paper(
            session,
            PaperRecord(
                title="Closed Nature paper",
                doi="10.1038/example-oa",
                pdf_url="https://www.nature.com/articles/example",
                status=PaperStatus.PAYWALLED,
            ),
        )
        paper_id = paper.id

    async def fake_resolve(self, record):
        record.pdf_url = "https://arxiv.org/pdf/1234.5678.pdf"
        record.open_access = True
        record.status = PaperStatus.OA_AVAILABLE
        return record

    async def fake_download(self, paper_id, paper, topic_slug, **kwargs):
        paper.status = PaperStatus.DOWNLOADED
        return paper

    monkeypatch.setattr("app.services.download_service.OpenAccessService.resolve", fake_resolve)
    monkeypatch.setattr("app.services.download_service.DownloadService.download_paper", fake_download)

    stats = await recheck_paywalled_open_access()
    assert stats["checked"] == 1
    assert stats["found"] == 1
    assert stats["downloaded"] == 1
    assert stats["still_closed"] == 0

    with session_scope() as session:
        row = session.get(Paper, paper_id)
        assert row is not None
        assert row.pdf_url == "https://arxiv.org/pdf/1234.5678.pdf"


@pytest.mark.asyncio
async def test_recheck_leaves_closed_when_unpaywall_has_no_pdf(tmp_db, monkeypatch):
    with session_scope() as session:
        save_paper(
            session,
            PaperRecord(
                title="Still gated",
                doi="10.1038/still-closed",
                pdf_url="https://www.nature.com/articles/still-closed",
                status=PaperStatus.PAYWALLED,
            ),
        )

    async def fake_resolve(self, record):
        record.pdf_url = None
        record.open_access = False
        record.status = PaperStatus.PAYWALLED
        return record

    monkeypatch.setattr("app.services.download_service.OpenAccessService.resolve", fake_resolve)

    stats = await recheck_paywalled_open_access()
    assert stats["checked"] == 1
    assert stats["found"] == 0
    assert stats["downloaded"] == 0
    assert stats["still_closed"] == 1

    with session_scope() as session:
        row = session.scalar(select(Paper).where(Paper.doi == "10.1038/still-closed"))
        assert row is not None
        assert row.pdf_url is None
        assert row.status == PaperStatus.PAYWALLED.value


@pytest.mark.asyncio
async def test_ensure_local_pdf_asks_unpaywall_for_paywalled(tmp_db, monkeypatch):
    with session_scope() as session:
        paper = save_paper(
            session,
            PaperRecord(title="Paywalled one-off", doi="10.1038/one-off", status=PaperStatus.PAYWALLED),
        )
        paper_id = paper.id

    seen: list[str | None] = []

    async def fake_resolve(self, record):
        seen.append(record.doi)
        record.pdf_url = None
        record.status = PaperStatus.PAYWALLED
        return record

    monkeypatch.setattr("app.services.download_service.OpenAccessService.resolve", fake_resolve)

    with pytest.raises(DownloadError, match="No legally available"):
        await ensure_local_pdf(paper_id)
    assert seen == ["10.1038/one-off"]


def test_library_recheck_oa_enqueues(tmp_db, monkeypatch):
    calls: list[dict] = []

    def fake_enqueue(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr("app.web.routes.library.enqueue_oa_recheck", fake_enqueue)
    monkeypatch.setattr("app.web.routes.library.oa_download_active", lambda: False)

    with session_scope() as session:
        paper = save_paper(
            session,
            PaperRecord(title="Closed Nature paper", doi="10.1038/ui-recheck", status=PaperStatus.PAYWALLED),
        )
        paper_id = paper.id

    client = login_admin(TestClient(app))
    page = client.get("/library?status=PAYWALLED")
    assert page.status_code == 200
    assert "Re-check Unpaywall" in page.text
    assert "Re-check OA" in page.text

    bulk = client.post("/library/recheck-oa", follow_redirects=False)
    assert bulk.status_code in {302, 303}
    assert bulk.headers["location"] == "/downloads"
    assert calls[0].get("paper_id") is None

    one = client.post(f"/papers/{paper_id}/recheck-oa", follow_redirects=False)
    assert one.status_code in {302, 303}
    assert one.headers["location"] == "/downloads"
    assert calls[-1]["paper_id"] == paper_id

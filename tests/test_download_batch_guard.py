"""Download tracker ownership must not finish another batch mid-flight."""

from __future__ import annotations

import asyncio

import pytest

from app.models.paper import PaperRecord, PaperStatus
from app.services.download_service import DownloadService, download_papers_parallel
from app.services.progress import (
    clear_download_batch_owner,
    clear_download_halt,
    download_tracker,
    release_download_batch,
    try_claim_download_batch,
)


@pytest.fixture(autouse=True)
def _reset_download_tracker():
    clear_download_halt()
    clear_download_batch_owner()
    download_tracker.reset()
    yield
    clear_download_halt()
    clear_download_batch_owner()
    download_tracker.reset()


def test_try_claim_rejects_second_owner():
    first = try_claim_download_batch(2, "first")
    assert first is not None
    assert try_claim_download_batch(1, "second") is None
    release_download_batch(first)
    assert download_tracker.snapshot()["active"] is False
    second = try_claim_download_batch(1, "second")
    assert second is not None
    release_download_batch(second)


def test_release_ignores_foreign_token():
    token = try_claim_download_batch(1, "owned")
    release_download_batch(object())
    assert download_tracker.snapshot()["active"] is True
    release_download_batch(token)
    assert download_tracker.snapshot()["active"] is False


@pytest.mark.asyncio
async def test_parallel_batches_serialize_finish(monkeypatch):
    """A second Downloads-page batch waits; finish_batch only runs once the owner ends."""

    class _Client:
        pass

    provider = DownloadService(_Client())  # type: ignore[arg-type]
    provider.config.env.max_concurrent_downloads = 2
    events: list[str] = []

    async def fake_download(paper_id, paper, topic_slug, **kwargs):
        events.append(f"start-{paper_id}")
        await asyncio.sleep(0.05)
        paper.status = PaperStatus.DOWNLOADED
        events.append(f"done-{paper_id}")
        return paper

    monkeypatch.setattr(provider, "download_paper", fake_download)

    async def batch_a():
        jobs = [(1, PaperRecord(title="A1", source_provider="test"))]
        await download_papers_parallel(provider, jobs, topic_slug="t", use_download_tracker=True)
        events.append("a-finished")

    async def batch_b():
        await asyncio.sleep(0.01)
        jobs = [
            (2, PaperRecord(title="B1", source_provider="test")),
            (3, PaperRecord(title="B2", source_provider="test")),
        ]
        await download_papers_parallel(provider, jobs, topic_slug="t", use_download_tracker=True)
        events.append("b-finished")

    await asyncio.gather(batch_a(), batch_b())
    assert events.index("a-finished") < events.index("start-2")
    assert events.index("a-finished") < events.index("b-finished")
    assert download_tracker.snapshot()["active"] is False
    messages = [entry["message"] for entry in download_tracker.snapshot()["logs"]]
    assert any(m.startswith("Finished:") for m in messages)
    assert download_tracker.snapshot()["downloaded"] == 2


def test_robots_allowed_uses_runtime_overlay(monkeypatch):
    from app.config import load_config
    from app.utils import security

    security._robots_cache.clear()
    cfg = load_config().model_copy(deep=True)
    cfg.check_robots_txt = False
    monkeypatch.setattr("app.utils.security.get_runtime_config", lambda: cfg)
    assert security.robots_allowed("https://www.mdpi.com/foo.pdf", "CyberScholar/1.0") is True


def test_robots_allowed_times_out_without_hanging(monkeypatch):
    from app.config import load_config
    from app.utils import security

    security._robots_cache.clear()
    cfg = load_config().model_copy(deep=True)
    cfg.check_robots_txt = True
    monkeypatch.setattr("app.utils.security.get_runtime_config", lambda: cfg)

    def boom(*_args, **_kwargs):
        raise TimeoutError("simulated hang")

    monkeypatch.setattr("app.utils.security.urllib.request.urlopen", boom)
    assert security.robots_allowed("https://hung.example/file.pdf", "CyberScholar/1.0") is True
    assert security._robots_cache.get("https://hung.example/robots.txt") is None
    # Cached miss must not call urlopen again.
    monkeypatch.setattr(
        "app.utils.security.urllib.request.urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should use cache")),
    )
    assert security.robots_allowed("https://hung.example/other.pdf", "CyberScholar/1.0") is True


@pytest.mark.asyncio
async def test_parallel_with_job_progress_skips_busy_download_tracker(monkeypatch):
    """Search/crawl must not hang at 82% when Downloads-page batch is owned."""
    from app.services.progress import ProgressTracker

    class _Client:
        pass

    provider = DownloadService(_Client())  # type: ignore[arg-type]
    provider.config.env.max_concurrent_downloads = 1

    async def fake_download(paper_id, paper, topic_slug, **kwargs):
        paper.status = PaperStatus.DOWNLOADED
        return paper

    monkeypatch.setattr(provider, "download_paper", fake_download)

    owner = try_claim_download_batch(5, "Downloads page busy")
    assert owner is not None
    job = ProgressTracker()
    job.start_search("busy-tracker")
    try:
        results = await asyncio.wait_for(
            download_papers_parallel(
                provider,
                [(9, PaperRecord(title="S1", source_provider="test"))],
                topic_slug="t",
                use_download_tracker=True,
                job_progress=job,
            ),
            timeout=5.0,
        )
    finally:
        release_download_batch(owner)

    assert results[0][1].status == PaperStatus.DOWNLOADED
    assert download_tracker.snapshot()["active"] is False
    assert any("busy" in (e.get("message") or "").lower() for e in job.snapshot().get("logs", []))

from pathlib import Path

from app.services.download_service import safe_library_pdf
from app.services.progress import ProgressTracker


def test_search_logs_stay_separate_from_download():
    search = ProgressTracker()
    download = ProgressTracker()
    search.start_search("machine learning IDS")
    snap = search.snapshot()
    assert snap["active"] is True
    assert snap["kind"] == "search"
    assert snap["phase"] == "starting"
    assert snap["logs"]
    search.set_providers_total(2)
    search.set_phase("searching", "Querying 2 academic sources…", percent=8)
    search.provider_finished("OpenAlex", 12)
    search.provider_finished("arXiv", 4)
    searching = search.snapshot()
    assert searching["providers_done"] == 2
    assert any("OpenAlex: 12 results" in entry["message"] for entry in searching["logs"])

    search.set_phase(
        "downloading",
        "Downloading 1 open-access PDF…",
        current=0,
        total=1,
        percent=82,
    )
    download.start_batch(1, "Downloading open-access PDFs")
    search_during_pdfs = search.snapshot()
    download_during_pdfs = download.snapshot()
    assert search_during_pdfs["kind"] == "search"
    assert search_during_pdfs["active"] is True
    assert search_during_pdfs["phase"] == "downloading"
    assert any("Queued search" in entry["message"] for entry in search_during_pdfs["logs"])
    assert download_during_pdfs["kind"] == "download"
    assert not any("Queued search" in entry["message"] for entry in download_during_pdfs["logs"])

    download.begin_item(1, "A paper", 1)
    download.log("GET https://example.com/paper.pdf")
    download.update_bytes(50, 100)
    download.finish_item("DOWNLOADED")
    download.finish_batch()
    search.log("PDF downloads: 1 saved, 0 failed, 0 skipped")
    search.finish_search(stats={"unique_papers": 3, "pdfs_downloaded": 1})

    search_messages = [entry["message"] for entry in search.snapshot()["logs"]]
    download_messages = [entry["message"] for entry in download.snapshot()["logs"]]
    assert any("OpenAlex: 12 results" in msg for msg in search_messages)
    assert any("PDF downloads:" in msg for msg in search_messages)
    assert not any("GET " in msg for msg in search_messages)
    assert not any("1/1: A paper" in msg for msg in search_messages)
    assert any("GET https://example.com/paper.pdf" in msg for msg in download_messages)
    assert any("Saved" in msg and "A paper" in msg for msg in download_messages)
    assert not any("Queued search" in msg for msg in download_messages)

    done = search.snapshot()
    assert done["active"] is False
    assert done["phase"] == "done"
    assert done["percent"] == 100
    assert done["stats"]["unique_papers"] == 3


def test_download_start_does_not_keep_search_logs():
    tracker = ProgressTracker()
    tracker.start_search("queued topic")
    tracker.start_batch(1, "Downloading")
    snap = tracker.snapshot()
    assert snap["kind"] == "download"
    assert snap["active"] is True
    messages = [entry["message"] for entry in snap["logs"]]
    assert not any("Queued search" in msg for msg in messages)
    assert any("Downloading" in msg for msg in messages)


def test_progress_cancel_marks_stopped():
    tracker = ProgressTracker()
    tracker.start_search("stop this")
    tracker.request_cancel()
    assert tracker.is_cancelled() is True
    tracker.finish_search(cancelled=True)
    snap = tracker.snapshot()
    assert snap["active"] is False
    assert snap["phase"] == "cancelled"
    assert snap["cancelled"] is True
    assert "stopped" in snap["message"].lower()


def test_live_progress_prefers_download_over_search():
    from app.services.progress import download_tracker, live_progress, tracker

    tracker.start_search("topic")
    download_tracker.start_batch(1, "Downloading")
    try:
        snap = live_progress()
        assert snap["kind"] == "download"
        assert snap["active"] is True
        download_tracker.finish_batch()
        snap = live_progress()
        assert snap["kind"] == "search"
        assert snap["active"] is True
    finally:
        if download_tracker.snapshot().get("active"):
            download_tracker.finish_batch()
        if tracker.snapshot().get("active"):
            tracker.finish_search()


def test_progress_percent_and_batch():
    tracker = ProgressTracker()
    tracker.start_batch(2, "Downloading")
    tracker.begin_item(1, "First paper", 1)
    tracker.update_bytes(50, 100)
    snap = tracker.snapshot()
    assert snap["active"] is True
    assert snap["percent"] == 50.0
    tracker.finish_item("DOWNLOADED")
    tracker.begin_item(2, "Second paper", 2)
    tracker.update_bytes(10, None)
    tracker.finish_item("FAILED", error="HTTP 403")
    tracker.finish_batch()
    done = tracker.snapshot()
    assert done["active"] is False
    assert done["downloaded"] == 1
    assert done["failed"] == 1
    assert "Finished" in done["message"]
    messages = [entry["message"] for entry in done["logs"]]
    assert any("1/2: First paper" in msg for msg in messages)
    assert any("Saved" in msg and "First paper" in msg for msg in messages)
    assert any("Failed" in msg and "HTTP 403" in msg for msg in messages)


def test_safe_library_pdf_rejects_escape(tmp_path: Path, monkeypatch):
    library = tmp_path / "research_library"
    library.mkdir()
    good = library / "paper.pdf"
    good.write_bytes(b"%PDF-1.7" + b"0" * 100)
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.7" + b"0" * 100)

    from app.config import load_config

    load_config.cache_clear()
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "x.db"))
    # Point library via a fake config by passing library_root explicitly.
    assert safe_library_pdf(str(good), library) == good.resolve()
    assert safe_library_pdf(str(outside), library) is None
    assert safe_library_pdf("../secret.pdf", library) is None

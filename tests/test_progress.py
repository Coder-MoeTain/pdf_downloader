from pathlib import Path

from app.services.download_service import safe_library_pdf
from app.services.progress import ProgressTracker


def test_search_logs_and_nested_download():
    tracker = ProgressTracker()
    tracker.start_search("machine learning IDS")
    snap = tracker.snapshot()
    assert snap["active"] is True
    assert snap["kind"] == "search"
    assert snap["phase"] == "starting"
    assert snap["logs"]
    tracker.set_providers_total(2)
    tracker.set_phase("searching", "Querying 2 academic sources…", percent=8)
    tracker.provider_finished("OpenAlex", 12)
    tracker.provider_finished("arXiv", 4)
    searching = tracker.snapshot()
    assert searching["providers_done"] == 2
    assert any("OpenAlex: 12 results" in entry["message"] for entry in searching["logs"])

    tracker.start_batch(1, "Downloading open-access PDFs")
    nested = tracker.snapshot()
    assert nested["kind"] == "search"
    assert nested["active"] is True
    assert nested["phase"] == "downloading"
    assert nested["percent"] == 82
    assert any("Queued search" in entry["message"] for entry in nested["logs"])
    tracker.begin_item(1, "A paper", 1)
    tracker.update_bytes(50, 100)
    bytes_snap = tracker.snapshot()
    assert 82 <= bytes_snap["percent"] <= 96
    assert any("1/1: A paper" in entry["message"] for entry in bytes_snap["logs"])
    tracker.finish_item("DOWNLOADED")
    tracker.finish_batch()
    after_pdfs = tracker.snapshot()
    assert after_pdfs["active"] is True
    assert after_pdfs["kind"] == "search"
    tracker.finish_search(stats={"unique_papers": 3, "pdfs_downloaded": 1})
    done = tracker.snapshot()
    assert done["active"] is False
    assert done["phase"] == "done"
    assert done["percent"] == 100
    assert done["stats"]["unique_papers"] == 3


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

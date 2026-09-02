from pathlib import Path

from app.services.download_service import safe_library_pdf
from app.services.progress import ProgressTracker


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
    tracker.finish_item("FAILED")
    tracker.finish_batch()
    done = tracker.snapshot()
    assert done["active"] is False
    assert done["downloaded"] == 1
    assert done["failed"] == 1
    assert "Finished" in done["message"]


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

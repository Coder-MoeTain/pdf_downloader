from app.database.models import Download, Paper
from app.models.paper import PaperStatus
from app.services.download_service import has_claimed_local_pdf, pdf_button_state, pdf_header_ok


def test_pdf_button_paywalled():
    paper = Paper(title="Closed", status="PAYWALLED", pdf_url=None)
    assert pdf_button_state(paper) == "paywalled"


def test_pdf_button_available_url():
    paper = Paper(title="OA", status="OA_AVAILABLE", pdf_url="https://arxiv.org/pdf/1234.5678.pdf")
    assert pdf_button_state(paper) == "download"


def test_pdf_button_unavailable():
    paper = Paper(title="None", status="NO_PDF", pdf_url=None)
    assert pdf_button_state(paper) == "unavailable"


def test_pdf_button_trusts_claimed_local_path_without_disk():
    paper = Paper(title="Saved", status="DOWNLOADED", pdf_url=None)
    paper.downloads = [
        Download(status=PaperStatus.DOWNLOADED.value, local_path="/library/missing-but-claimed.pdf")
    ]
    assert has_claimed_local_pdf(paper) is True
    assert pdf_button_state(paper) == "download"


def test_pdf_header_ok_reads_only_prefix(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-" + (b"x" * 2_000_000))
    assert pdf_header_ok(path, min_size=1) is True

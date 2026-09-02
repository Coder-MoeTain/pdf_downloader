from app.database.models import Paper
from app.services.download_service import pdf_button_state


def test_pdf_button_paywalled():
    paper = Paper(title="Closed", status="PAYWALLED", pdf_url=None)
    assert pdf_button_state(paper) == "paywalled"


def test_pdf_button_available_url():
    paper = Paper(title="OA", status="OA_AVAILABLE", pdf_url="https://arxiv.org/pdf/1234.5678.pdf")
    assert pdf_button_state(paper) == "download"


def test_pdf_button_unavailable():
    paper = Paper(title="None", status="NO_PDF", pdf_url=None)
    assert pdf_button_state(paper) == "unavailable"

from app.services.oa_service import _usable_pdf
from app.utils.pdf_url import is_direct_pdf_url, is_doi_resolver_url


def test_doi_landing_pages_are_not_pdfs():
    assert is_doi_resolver_url("https://doi.org/10.1016/j.cose.2023.103532")
    assert is_doi_resolver_url("http://dx.doi.org/10.47392/irjaem.2024.0390")
    assert not is_direct_pdf_url("https://doi.org/10.1016/j.cose.2023.103532")
    assert not is_direct_pdf_url("http://dx.doi.org/10.47392/irjaem.2024.0390", prefer_https=False)
    assert not is_direct_pdf_url("https://doi.org/10.1109/access.2021.3125785")
    assert not is_direct_pdf_url("https://doi.org/10.2139/ssrn.4602444")
    assert not _usable_pdf("https://doi.org/10.14429/dsj.62.1291")


def test_real_pdf_urls_are_accepted():
    assert is_direct_pdf_url("https://arxiv.org/pdf/2301.00001.pdf")
    assert is_direct_pdf_url("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123456/pdf/")
    assert is_direct_pdf_url("https://journals.plos.org/plosone/article/file?id=10.1371/x&type=printable")
    assert is_direct_pdf_url("https://openreview.net/pdf?id=abc")
    assert _usable_pdf("https://arxiv.org/pdf/2301.00001.pdf")


def test_publisher_html_pages_are_rejected():
    assert not is_direct_pdf_url("https://www.sciencedirect.com/science/article/pii/S0167404823001234")
    assert not is_direct_pdf_url("https://ieeexplore.ieee.org/document/9591234")
    assert not is_direct_pdf_url("https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4602444")

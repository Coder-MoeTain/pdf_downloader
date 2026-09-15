from types import SimpleNamespace

from app.models.paper import AuthorRecord, PaperRecord
from app.services.citation import paper_citations


def test_apa_mla_chicago_and_bibtex_from_paper_record():
    paper = PaperRecord(
        title="Satellites can observe drought from orbit",
        authors=[AuthorRecord(name="Ada Lovelace"), AuthorRecord(name="Charles Babbage")],
        publication_year=2024,
        journal="Remote Sensing",
        volume="16",
        issue="4",
        pages="12-20",
        doi="10.1000/abs-preview",
    )
    cites = paper_citations(paper)
    assert cites["apa"] == (
        "Lovelace, A., & Babbage, C. (2024). Satellites can observe drought from orbit. "
        "Remote Sensing 16(4), 12-20. https://doi.org/10.1000/abs-preview"
    )
    assert cites["mla"].startswith("Lovelace, Ada, and Charles Babbage.")
    assert '"Satellites can observe drought from orbit."' in cites["mla"]
    assert "vol. 16, no. 4, 2024, pp. 12-20." in cites["mla"]
    assert cites["chicago"].startswith("Lovelace, Ada, and Charles Babbage.")
    assert "Remote Sensing 16, no. 4 (2024): 12-20." in cites["chicago"]
    assert cites["bibtex"].startswith("@article{lovelace2024satellites,")
    assert "author = {Lovelace, Ada and Babbage, Charles}" in cites["bibtex"]
    assert "doi = {10.1000/abs-preview}" in cites["bibtex"]


def test_et_al_and_arxiv_fallback():
    paper = PaperRecord(
        title="A survey",
        authors=[
            AuthorRecord(name="Min Lin"),
            AuthorRecord(name="Kang An"),
            AuthorRecord(name="Haitham Cruickshank"),
        ],
        publication_year=2021,
        arxiv_id="2101.00001",
    )
    cites = paper_citations(paper)
    assert cites["apa"].startswith("Lin, M., An, K., & Cruickshank, H. (2021).")
    assert cites["mla"].startswith("Lin, Min, et al.")
    assert "https://arxiv.org/abs/2101.00001" in cites["apa"]
    assert cites["bibtex"].startswith("@misc{lin2021a,")
    assert "eprint = {2101.00001}" in cites["bibtex"]


def test_citations_from_orm_style_authors():
    author = SimpleNamespace(name="John von Neumann")
    link = SimpleNamespace(author=author, position=0)
    paper = SimpleNamespace(
        title="First draft of a report on the EDVAC",
        authors=[link],
        publication_year=1945,
        journal=None,
        conference=None,
        volume=None,
        issue=None,
        pages=None,
        publisher="Moore School",
        doi=None,
        arxiv_id=None,
        url="https://example.edu/edvac",
    )
    cites = paper_citations(paper)
    assert cites["apa"].startswith("von Neumann, J. (1945).")
    assert "Moore School." in cites["apa"]
    assert cites["apa"].endswith("https://example.edu/edvac")

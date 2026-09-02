from app.providers.crossref import CrossrefProvider
from app.providers.openalex import OpenAlexProvider, inverted_index_to_text
from app.providers.semantic_scholar import SemanticScholarProvider


class _Dummy:
    pass


def test_crossref_parse():
    provider = CrossrefProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "DOI": "10.1145/example",
            "title": ["A Study of Web Security"],
            "author": [{"given": "Alan", "family": "Turing", "affiliation": [{"name": "Manchester"}]}],
            "published-print": {"date-parts": [[2024, 5, 1]]},
            "container-title": ["CCS"],
            "publisher": "ACM",
            "is-referenced-by-count": 11,
            "URL": "https://doi.org/10.1145/example",
            "type": "proceedings-article",
            "event": {"name": "CCS"},
            "link": [{"URL": "https://example.com/paper.pdf", "content-type": "application/pdf"}],
        }
    )
    assert paper is not None
    assert paper.doi == "10.1145/example"
    assert paper.authors[0].name == "Alan Turing"
    assert paper.publication_year == 2024
    assert paper.pdf_url.endswith(".pdf")


def test_openalex_abstract_and_parse():
    inv = {"Machine": [0], "learning": [1], "security": [2]}
    assert inverted_index_to_text(inv) == "Machine learning security"
    provider = OpenAlexProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "display_name": "OpenAlex Work",
            "publication_year": 2023,
            "doi": "https://doi.org/10.1234/oa",
            "ids": {"openalex": "https://openalex.org/W1"},
            "id": "https://openalex.org/W123",
            "cited_by_count": 3,
            "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/pdf/1.pdf"},
            "authorships": [{"author": {"display_name": "Ada"}, "institutions": [{"display_name": "MIT"}]}],
        }
    )
    assert paper.doi == "10.1234/oa"
    assert paper.open_access is True
    assert paper.authors[0].affiliations == ["MIT"]


def test_semantic_scholar_parse():
    provider = SemanticScholarProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "paperId": "abc",
            "title": "S2 Paper",
            "abstract": "Hello",
            "year": 2022,
            "authors": [{"name": "Grace Hopper"}],
            "externalIds": {"DOI": "10.1/s2", "ArXiv": "2201.00001"},
            "citationCount": 9,
            "openAccessPdf": {"url": "https://arxiv.org/pdf/2201.00001.pdf"},
        }
    )
    assert paper.semantic_scholar_id == "abc"
    assert paper.arxiv_id == "2201.00001"
    assert paper.citation_count == 9

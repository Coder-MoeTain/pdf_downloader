from app.providers import PROVIDER_CLASSES
from app.providers.crossref import CrossrefProvider
from app.providers.extra import NasaAdsProvider, NasaNtrsProvider
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


def test_nasa_ntrs_is_registered():
    names = {cls.name for cls in PROVIDER_CLASSES}
    assert "nasa_ntrs" in names
    assert "nasa_ads" in names


def test_nasa_ntrs_parse_pdf_and_authors():
    provider = NasaNtrsProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "id": 20020038863,
            "title": "Microheater Array Boiling Experiment",
            "abstract": "Pool boiling in microgravity.",
            "stiType": "TECHNICAL_MEMORANDUM",
            "disseminated": "DOCUMENT_AND_METADATA",
            "distributionDate": "2019-07-12T00:00:00.0000000+00:00",
            "keywords": ["Boiling", "Microgravity"],
            "subjectCategories": ["Fluid Mechanics And Thermodynamics"],
            "center": {"name": "Glenn Research Center"},
            "copyright": {"determinationType": "GOV_PUBLIC_USE_PERMITTED"},
            "authorAffiliations": [
                {
                    "meta": {
                        "author": {"name": "Kim, Jungho"},
                        "organization": {"name": "Maryland Univ."},
                    }
                },
                {
                    "meta": {
                        "author": {"name": "McQuillen, John", "orcidId": "0000-0001-2345-6789"},
                        "organization": {"name": "NASA Glenn Research Center"},
                    }
                },
            ],
            "publications": [{"publicationDate": "2002-01-01T00:00:00.0000000+00:00", "doi": "10.2514/6.2001-5116"}],
            "downloads": [
                {
                    "mimetype": "application/pdf",
                    "name": "20020038863.pdf",
                    "links": {
                        "original": "/api/citations/20020038863/downloads/20020038863.pdf",
                        "pdf": "/api/citations/20020038863/downloads/20020038863.pdf",
                    },
                }
            ],
        }
    )
    assert paper is not None
    assert paper.doi == "10.2514/6.2001-5116"
    assert paper.publication_year == 2002
    assert paper.authors[0].name == "Kim, Jungho"
    assert paper.authors[1].affiliations == ["NASA Glenn Research Center"]
    assert paper.pdf_url == "https://ntrs.nasa.gov/api/citations/20020038863/downloads/20020038863.pdf"
    assert paper.url == "https://ntrs.nasa.gov/citations/20020038863"
    assert paper.open_access is True
    assert paper.publisher == "Glenn Research Center"


def test_nasa_ntrs_ignores_non_pdf_downloads():
    provider = NasaNtrsProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "id": 20250001688,
            "title": "Digital Twins",
            "stiType": "CONFERENCE_PAPER",
            "disseminated": "DOCUMENT_AND_METADATA",
            "authorAffiliations": [{"meta": {"author": {"name": "Robert J Brown"}}}],
            "meetings": [{"name": "Ground System Architectures Workshop (GSAW)"}],
            "downloads": [
                {
                    "mimetype": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    "name": "GSAW_2025_Slides.pptx",
                    "links": {"original": "/api/citations/20250001688/downloads/GSAW_2025_Slides.pptx"},
                }
            ],
        }
    )
    assert paper is not None
    assert paper.pdf_url is None
    assert paper.conference == "Ground System Architectures Workshop (GSAW)"


def test_nasa_ads_uses_esource_pdf():
    provider = NasaAdsProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "title": ["Satellite Cybersecurity Survey"],
            "author": ["Smith, A."],
            "year": "2024",
            "doi": ["10.3847/example"],
            "bibcode": "2024ApJ...900...1S",
            "abstract": "A survey.",
            "pub": "ApJ",
            "citation_count": 12,
            "openaccess": False,
            "esources": ["EPRINT_PDF", "PUB_HTML"],
        }
    )
    assert paper is not None
    assert paper.doi == "10.3847/example"
    assert paper.pdf_url == "https://ui.adsabs.harvard.edu/link_gateway/2024ApJ...900...1S/EPRINT_PDF"
    assert paper.open_access is True
    assert paper.citation_count == 12

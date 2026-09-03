import pytest

from app.models.paper import PaperRecord
from app.models.search import SearchFilters
from app.providers import PROVIDER_CLASSES
from app.providers.crossref import CrossrefProvider
from app.providers.extra import NasaAdsProvider, NasaNtrsProvider
from app.providers.free import (
    DoabProvider,
    EconstorProvider,
    InspireProvider,
    OpenaireProvider,
    OsfProvider,
    PlosProvider,
    ZenodoProvider,
)
from app.providers.more import (
    ChemrxivProvider,
    ElifeProvider,
    FaoProvider,
    OpenreviewProvider,
    PeerjProvider,
    UsgsProvider,
)
from app.providers.openalex import OpenAlexProvider, inverted_index_to_text
from app.providers.pubmed import PmcProvider
from app.providers.semantic_scholar import SemanticScholarProvider
from app.utils.http import HttpError

FREE_SOURCE_SLUGS = {
    "openaire",
    "hal",
    "zenodo",
    "dblp",
    "plos",
    "eric",
    "osti",
    "datacite",
    "osf",
    "biorxiv",
    "medrxiv",
    "figshare",
    "doab",
    "scielo",
    "cinii",
    "inspire",
    "fatcat",
    "worldbank",
        "oapen",
        "econstor",
}

NEW_SOURCE_SLUGS = {
    "pmc",
    "chemrxiv",
    "ssrn",
    "research_square",
    "techrxiv",
    "peerj",
    "f1000research",
    "nber",
    "eartharxiv",
    "openreview",
    "elife",
    "scipost",
    "paperswithcode",
    "zbmath",
    "usgs",
    "dataverse",
    "fao",
    "who",
    "cern",
    "ndl",
}


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


def test_free_sources_are_registered():
    names = {cls.name for cls in PROVIDER_CLASSES}
    assert FREE_SOURCE_SLUGS <= names
    assert NEW_SOURCE_SLUGS <= names
    assert len(FREE_SOURCE_SLUGS) == 20
    assert len(NEW_SOURCE_SLUGS) == 20
    from app.database.source_catalog import BUILTIN_SOURCES

    catalog = {str(item["slug"]) for item in BUILTIN_SOURCES}
    assert FREE_SOURCE_SLUGS <= catalog
    assert NEW_SOURCE_SLUGS <= catalog
    assert catalog == names


def test_openaire_parse_oa_pdf():
    provider = OpenaireProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "mainTitle": "Open Science Infrastructure",
            "authors": [{"fullName": "Ada Lovelace"}],
            "publicationYear": 2021,
            "publicationDate": "2021-03-01",
            "descriptions": ["A survey of open repositories."],
            "pids": [{"scheme": "doi", "value": "10.5281/zenodo.111"}],
            "bestAccessRight": {"code": "OPEN", "label": "Open Access"},
            "instances": [{"urls": ["https://zenodo.org/records/111/files/paper.pdf"]}],
            "container": {"name": "Open Research"},
            "publisher": "OpenAIRE",
        }
    )
    assert paper is not None
    assert paper.doi == "10.5281/zenodo.111"
    assert paper.authors[0].name == "Ada Lovelace"
    assert paper.pdf_url.endswith(".pdf")
    assert paper.open_access is True


def test_zenodo_parse_pdf_file():
    provider = ZenodoProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "id": 99,
            "metadata": {
                "title": "A Zenodo Preprint",
                "description": "Methods",
                "creators": [{"name": "Hopper, Grace"}],
                "publication_date": "2024-02-10",
                "doi": "10.5281/zenodo.99",
                "keywords": ["security"],
            },
            "files": [
                {
                    "key": "paper.pdf",
                    "mimetype": "application/pdf",
                    "links": {"self": "https://zenodo.org/api/files/abc/paper.pdf"},
                }
            ],
            "links": {"html": "https://zenodo.org/records/99"},
        }
    )
    assert paper is not None
    assert paper.pdf_url == "https://zenodo.org/api/files/abc/paper.pdf"
    assert paper.publication_year == 2024
    assert paper.open_access is True


def test_plos_builds_printable_pdf():
    provider = PlosProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "id": "10.1371/journal.pone.0123456",
            "title": "Open Access Methods",
            "author": ["Doe, Jane"],
            "abstract": "We describe a method.",
            "publication_date": "2023-05-01T00:00:00Z",
            "journal": "PLoS ONE",
            "doi": "10.1371/journal.pone.0123456",
        }
    )
    assert paper is not None
    assert paper.open_access is True
    assert paper.pdf_url == (
        "https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0123456&type=printable"
    )


def test_inspire_prefers_document_then_arxiv():
    provider = InspireProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "id": 1234567,
            "metadata": {
                "titles": [{"title": "Collider Phenomenology"}],
                "authors": [{"full_name": "Yang, C. N."}],
                "abstracts": [{"value": "A review."}],
                "dois": [{"value": "10.1103/PhysRev.96.191"}],
                "arxiv_eprints": [{"value": "hep-th/0001001"}],
                "earliest_date": "2020-01-15",
                "citation_count": 42,
                "documents": [{"url": "https://inspirehep.net/files/abc.pdf", "key": "fulltext.pdf"}],
            },
        }
    )
    assert paper is not None
    assert paper.arxiv_id == "hep-th/0001001"
    assert paper.pdf_url == "https://inspirehep.net/files/abc.pdf"
    assert paper.citation_count == 42


def test_doab_parses_dspace_metadata():
    provider = DoabProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "name": "Open Access Book",
            "handle": "20.500.12854/123",
            "metadata": [
                {"key": "dc.title", "value": "Open Access Book"},
                {"key": "dc.contributor.author", "value": "Smith, Ann"},
                {"key": "dc.date.issued", "value": "2022"},
                {"key": "dc.identifier.doi", "value": "10.11647/obp.0001"},
                {"key": "dc.description.abstract", "value": "A monograph."},
            ],
            "bitstreams": [
                {
                    "name": "book.pdf",
                    "mimeType": "application/pdf",
                    "retrieveLink": "/rest/bitstreams/uuid/retrieve",
                }
            ],
        }
    )
    assert paper is not None
    assert paper.doi == "10.11647/obp.0001"
    assert paper.authors[0].name == "Smith, Ann"
    assert paper.pdf_url == "https://directory.doabooks.org/rest/bitstreams/uuid/retrieve"
    assert paper.url.endswith("/handle/20.500.12854/123")


class _CaptureJson:
    def __init__(self, payload):
        self.payload = payload
        self.url = None
        self.params = None

    async def get_json(self, url, **kwargs):
        self.url = url
        self.params = kwargs.get("params") or {}
        return self.payload


@pytest.mark.asyncio
async def test_zenodo_caps_anonymous_page_size():
    client = _CaptureJson({"hits": {"hits": []}})
    provider = ZenodoProvider(client)  # type: ignore[arg-type]
    await provider.search("mathematic", SearchFilters(query="mathematic", max_results=300))
    assert client.params["size"] == 25
    assert client.params["q"] == "mathematic"


@pytest.mark.asyncio
async def test_econstor_uses_filtered_items():
    client = _CaptureJson({"items": []})
    provider = EconstorProvider(client)  # type: ignore[arg-type]
    await provider.search("mathematic", SearchFilters(query="mathematic", max_results=80))
    assert client.url.endswith("/rest/filtered-items")
    assert "dc.title" in client.params["query_field[]"]
    assert client.params["limit"] == 50


@pytest.mark.asyncio
async def test_osf_search_omits_embed():
    client = _CaptureJson({"data": []})
    provider = OsfProvider(client)  # type: ignore[arg-type]
    await provider.search("mathematic", SearchFilters(query="mathematic"))
    assert "embed" not in client.params
    assert client.params["filter[title]"] == "mathematic"


@pytest.mark.asyncio
async def test_semantic_scholar_429_mentions_api_key():
    class _Raise429:
        async def get_json(self, *args, **kwargs):
            raise HttpError("HTTP 429", 429)

    provider = SemanticScholarProvider(_Raise429())  # type: ignore[arg-type]
    with pytest.raises(HttpError, match="SEMANTIC_SCHOLAR_API_KEY"):
        await provider.search("mathematic", SearchFilters(query="mathematic"))


def test_pmc_parse_pdf_and_ids():
    provider = PmcProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "uid": "11789012",
            "title": "Open Access Genomics",
            "fulljournalname": "Scientific Reports",
            "pubdate": "2024 Jan 15",
            "authors": [{"name": "Ada Lovelace", "authtype": "Author"}],
            "articleids": [
                {"idtype": "doi", "value": "10.1038/s41598-024-0001"},
                {"idtype": "pmid", "value": "38123456"},
                {"idtype": "pmcid", "value": "PMC11789012"},
            ],
        }
    )
    assert paper is not None
    assert paper.doi == "10.1038/s41598-024-0001"
    assert paper.pmcid == "PMC11789012"
    assert paper.pmid == "38123456"
    assert paper.pdf_url == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11789012/pdf/"
    assert paper.open_access is True
    assert paper.authors[0].name == "Ada Lovelace"


def test_peerj_builds_pdf_from_doi():
    provider = PeerjProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._after_parse(
        PaperRecord(title="PeerJ Methods", doi="10.7717/peerj.1234", source_provider="peerj")
    )
    assert paper.pdf_url == "https://peerj.com/articles/1234.pdf"


def test_usgs_parse_pdf_link():
    provider = UsgsProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "id": 70150299,
            "indexId": "ofr20151076",
            "title": "Oil-particle interactions",
            "docAbstract": "<p>A review of the science.</p>",
            "publicationYear": "2015",
            "publisher": "U.S. Geological Survey",
            "doi": "10.3133/ofr20151076",
            "seriesTitle": {"text": "Open-File Report"},
            "contributors": {"authors": [{"given": "Faith A.", "family": "Fitzpatrick", "text": "Fitzpatrick, Faith A. fafitzpa@usgs.gov"}]},
            "links": [
                {
                    "url": "https://pubs.usgs.gov/of/2015/1076/pdf/ofr2015-1076.pdf",
                    "linkFileType": {"text": "pdf"},
                },
                {
                    "url": "https://pubs.usgs.gov/of/2015/1076/",
                    "type": {"text": "Index Page"},
                },
            ],
        }
    )
    assert paper is not None
    assert paper.doi == "10.3133/ofr20151076"
    assert paper.pdf_url.endswith(".pdf")
    assert paper.authors[0].name == "Faith A. Fitzpatrick"
    assert "review of the science" in (paper.abstract or "")
    assert paper.open_access is True


def test_elife_parse_pdf():
    provider = ElifeProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "id": "85537",
            "title": "Cell signalling",
            "authorLine": "A. Smith, B. Jones",
            "published": "2024-03-01",
            "doi": "10.7554/eLife.85537",
            "impactStatement": "A study of signalling.",
            "pdf": {"uri": "https://cdn.elifesciences.org/articles/85537/elife-85537-v1.pdf"},
        }
    )
    assert paper is not None
    assert paper.doi == "10.7554/elife.85537"
    assert paper.pdf_url.endswith(".pdf")
    assert paper.open_access is True
    assert paper.journal == "eLife"


def test_openreview_parse_forum_pdf():
    provider = OpenreviewProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "id": "abc123",
            "forum": "abc123",
            "pdate": 1_709_251_200_000,
            "content": {
                "title": {"value": "Learning to Search"},
                "abstract": {"value": "A conference paper."},
                "authors": {"value": ["Grace Hopper"]},
                "venue": {"value": "ICLR 2024"},
            },
        }
    )
    assert paper is not None
    assert paper.title == "Learning to Search"
    assert paper.authors[0].name == "Grace Hopper"
    assert paper.pdf_url == "https://openreview.net/pdf?id=abc123"
    assert paper.publication_year == 2024
    assert paper.open_access is True


def test_fao_parses_dspace7_metadata():
    provider = FaoProvider(_Dummy())  # type: ignore[arg-type]
    paper = provider._parse(
        {
            "_embedded": {
                "indexableObject": {
                    "name": "The State of Food Security",
                    "handle": "20.500.14283/cc123en",
                    "metadata": {
                        "dc.title": [{"value": "The State of Food Security"}],
                        "dc.contributor.author": [{"value": "FAO"}],
                        "dc.date.issued": [{"value": "2023"}],
                        "dc.identifier.doi": [{"value": "10.4060/cc123en"}],
                        "dc.description.abstract": [{"value": "A global report."}],
                    },
                }
            }
        }
    )
    assert paper is not None
    assert paper.doi == "10.4060/cc123en"
    assert paper.authors[0].name == "FAO"
    assert paper.url.endswith("/handle/20.500.14283/cc123en")
    assert paper.open_access is True


@pytest.mark.asyncio
async def test_chemrxiv_uses_crossref_container_filter():
    client = _CaptureJson({"message": {"items": []}})
    provider = ChemrxivProvider(client)  # type: ignore[arg-type]
    await provider.search("catalysis", SearchFilters(query="catalysis", max_results=20))
    assert client.params["filter"].startswith("container-title:ChemRxiv")
    assert client.params["rows"] == 20

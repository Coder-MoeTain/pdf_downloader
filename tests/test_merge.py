from app.models.paper import AuthorRecord, PaperRecord
from app.services.merge_service import merge_papers


def test_merge_prefers_authoritative_fields():
    crossref = PaperRecord(
        title="Short",
        doi="10.1000/xyz",
        publisher="IEEE",
        journal="TIFS",
        source_provider="crossref",
    )
    s2 = PaperRecord(
        title="A Longer Complete Title",
        abstract="An abstract from Semantic Scholar.",
        doi="10.1000/xyz",
        citation_count=42,
        authors=[AuthorRecord(name="Ada Lovelace")],
        source_provider="semantic_scholar",
    )
    oa = PaperRecord(
        title="A Longer Complete Title",
        abstract="short",
        open_access=True,
        pdf_url="https://arxiv.org/pdf/1234.5678.pdf",
        source_provider="openalex",
    )
    merged = merge_papers([crossref, s2, oa])
    assert merged.doi == "10.1000/xyz"
    assert merged.publisher == "IEEE"
    assert merged.journal == "TIFS"
    assert merged.abstract.startswith("An abstract")
    assert merged.citation_count == 42
    assert merged.open_access is True
    assert merged.pdf_url.endswith(".pdf")
    assert "crossref" in merged.source_provider
    assert merged.metadata_sources.get("publisher") == "crossref"

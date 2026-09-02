"""PubMed / NCBI E-utilities provider."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

from app.models.paper import AuthorRecord, PaperRecord
from app.models.search import SearchFilters
from app.providers.base import ResearchProvider
from app.utils.doi import normalize_doi
from app.utils.logger import get_logger

logger = get_logger("app.providers.pubmed")


class PubMedProvider(ResearchProvider):
    name = "pubmed"
    display_name = "PubMed"
    SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    FETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    def has_api_key(self) -> bool:
        return bool(self.config.env.ncbi_api_key)

    def _params(self, extra: dict[str, Any]) -> dict[str, Any]:
        params = {
            "db": "pubmed",
            "retmode": "xml",
            "tool": "ResearchPaperCollector",
            "email": self.config.env.polite_email,
            **extra,
        }
        if self.config.env.ncbi_api_key:
            params["api_key"] = self.config.env.ncbi_api_key
        return params

    async def search(self, query: str, filters: SearchFilters) -> list[PaperRecord]:
        term = query
        if filters.year_from or filters.year_to:
            start = filters.year_from or 1800
            end = filters.year_to or 2100
            term = f'({query}) AND ("{start}"[Date - Publication] : "{end}"[Date - Publication])'
        if filters.authors:
            term += f" AND {filters.authors}[Author]"
        if filters.journal:
            term += f" AND {filters.journal}[Journal]"

        search_xml = await self.request_text(
            self.SEARCH,
            params=self._params({"term": term, "retmax": min(filters.max_results, 100), "usehistory": "n"}),
        )
        ids = _extract_ids(search_xml)
        if not ids:
            return []
        fetch_xml = await self.request_text(
            self.FETCH,
            params=self._params({"id": ",".join(ids), "rettype": "abstract"}),
        )
        return _parse_pubmed_xml(fetch_xml, self.name)

    async def get_paper(self, identifier: str) -> PaperRecord | None:
        fetch_xml = await self.request_text(self.FETCH, params=self._params({"id": identifier, "rettype": "abstract"}))
        papers = _parse_pubmed_xml(fetch_xml, self.name)
        return papers[0] if papers else None

    async def find_pdf(self, paper: PaperRecord) -> str | None:
        if paper.pmcid:
            pmc = paper.pmcid if paper.pmcid.upper().startswith("PMC") else f"PMC{paper.pmcid}"
            return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc}/pdf/"
        return paper.pdf_url


def _extract_ids(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return [el.text for el in root.findall(".//Id") if el.text]


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return "".join(el.itertext()).strip() or None


def _parse_pubmed_xml(xml_text: str, source: str) -> list[PaperRecord]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning("Failed to parse PubMed XML")
        return []
    papers: list[PaperRecord] = []
    for article in root.findall(".//PubmedArticle"):
        medline = article.find("MedlineCitation")
        if medline is None:
            continue
        art = medline.find("Article")
        if art is None:
            continue
        title = _text(art.find("ArticleTitle")) or ""
        abstract_parts = [_text(a) for a in art.findall("./Abstract/AbstractText")]
        abstract = " ".join(p for p in abstract_parts if p) or None
        authors: list[AuthorRecord] = []
        for author in art.findall("./AuthorList/Author"):
            last = _text(author.find("LastName")) or ""
            fore = _text(author.find("ForeName")) or _text(author.find("Initials")) or ""
            name = f"{fore} {last}".strip() or _text(author.find("CollectiveName")) or ""
            if name:
                aff = [_text(a) for a in author.findall("./AffiliationInfo/Affiliation")]
                authors.append(AuthorRecord(name=name, affiliations=[a for a in aff if a]))
        journal = _text(art.find("./Journal/Title"))
        year_text = _text(art.find("./Journal/JournalIssue/PubDate/Year")) or _text(
            art.find("./Journal/JournalIssue/PubDate/MedlineDate")
        )
        year = None
        if year_text:
            digits = "".join(ch for ch in year_text[:4] if ch.isdigit())
            year = int(digits) if len(digits) == 4 else None
        pmid = _text(medline.find("PMID"))
        doi = None
        pmcid = None
        for aid in article.findall(".//ArticleId"):
            id_type = (aid.get("IdType") or "").lower()
            if id_type == "doi":
                doi = normalize_doi(aid.text)
            elif id_type == "pmc":
                pmcid = aid.text
        keywords = [_text(k) for k in medline.findall("./KeywordList/Keyword")]
        papers.append(
            PaperRecord(
                title=title,
                abstract=abstract,
                authors=authors,
                publication_year=year,
                journal=journal,
                volume=_text(art.find("./Journal/JournalIssue/Volume")),
                issue=_text(art.find("./Journal/JournalIssue/Issue")),
                pages=_text(art.find("Pagination/MedlinePgn")),
                publisher="National Library of Medicine",
                doi=doi,
                pmid=pmid,
                pmcid=pmcid,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                pdf_url=(
                    f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
                    if pmcid
                    else None
                ),
                keywords=[k for k in keywords if k],
                open_access=bool(pmcid),
                source_provider=source,
                metadata_sources={"pmid": source, "abstract": source},
            )
        )
    return papers

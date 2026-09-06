"""Detect legally available open-access PDFs. Never bypasses paywalls."""

from __future__ import annotations

from app.config import AppConfig, load_config
from app.models.paper import PaperRecord, PaperStatus
from app.utils.doi import normalize_doi
from app.utils.http import AsyncHttpClient, HttpError
from app.utils.logger import get_logger
from app.utils.pdf_url import is_direct_pdf_url, oa_url_priority

logger = get_logger("app.oa")

UNPAYWALL = "https://api.unpaywall.org/v2/{doi}"


class OpenAccessService:
    def __init__(self, client: AsyncHttpClient, config: AppConfig | None = None) -> None:
        self.client = client
        self.config = config or load_config()

    async def resolve(self, paper: PaperRecord) -> PaperRecord:
        if paper.arxiv_id and not _usable_pdf(paper.pdf_url):
            paper.pdf_url = f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf"
            paper.open_access = True
        if paper.pmcid and not _usable_pdf(paper.pdf_url):
            pmc = paper.pmcid if str(paper.pmcid).upper().startswith("PMC") else f"PMC{paper.pmcid}"
            # Prefer Europe PMC render URLs; NCBI often blocks bots via robots.txt.
            paper.pdf_url = f"https://europepmc.org/articles/{pmc}?pdf=render"
            paper.open_access = True

        if paper.doi and not _usable_pdf(paper.pdf_url):
            unpaywall = await self._unpaywall(paper.doi)
            if unpaywall:
                pdf, license_, is_oa = unpaywall
                paper.metadata_sources["open_access"] = "unpaywall"
                if pdf:
                    paper.pdf_url = pdf
                    paper.metadata_sources["pdf_url"] = "unpaywall"
                if license_:
                    paper.license = paper.license or license_
                if is_oa:
                    paper.open_access = True
                elif paper.open_access is None:
                    paper.open_access = False

        # Drop gated publisher CDNs left over from earlier metadata merges.
        if paper.pdf_url and not _usable_pdf(paper.pdf_url):
            paper.pdf_url = None

        if _usable_pdf(paper.pdf_url):
            paper.open_access = True
            paper.status = PaperStatus.OA_AVAILABLE
            return paper

        if paper.open_access is False or (paper.doi and paper.open_access is not True):
            paper.status = PaperStatus.PAYWALLED
            paper.open_access = False
            return paper

        if paper.open_access is True and not paper.pdf_url:
            paper.status = PaperStatus.NO_PDF
            return paper

        paper.status = PaperStatus.NO_PDF if not paper.doi else PaperStatus.PAYWALLED
        return paper

    async def alternate_pdf_url(self, paper: PaperRecord, *, exclude: str | None = None) -> str | None:
        """Pick another legal OA PDF URL, skipping a URL that already failed."""
        skip = {(exclude or "").strip(), (paper.pdf_url or "").strip()} - {""}
        candidates: list[str] = []
        if paper.arxiv_id:
            candidates.append(f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf")
        if paper.pmcid:
            pmc = paper.pmcid if str(paper.pmcid).upper().startswith("PMC") else f"PMC{paper.pmcid}"
            candidates.append(f"https://europepmc.org/articles/{pmc}?pdf=render")
            candidates.append(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc}/pdf/")
        if paper.doi:
            unpaywall = await self._unpaywall(paper.doi, collect_all=True)
            if unpaywall:
                pdfs, _license, _is_oa = unpaywall
                if isinstance(pdfs, list):
                    candidates.extend(pdfs)
                elif pdfs:
                    candidates.append(str(pdfs))
        ranked = sorted(
            {url for url in candidates if _usable_pdf(url) and url not in skip},
            key=oa_url_priority,
        )
        return ranked[0] if ranked else None

    async def _unpaywall(
        self,
        doi: str,
        *,
        collect_all: bool = False,
    ) -> tuple[str | list[str] | None, str | None, bool] | None:
        email = self.config.env.polite_email
        if not email or "example.com" in email:
            logger.info("Skipping Unpaywall: set CONTACT_EMAIL / UNPAYWALL_EMAIL to a real address")
            return None
        canonical = normalize_doi(doi)
        if not canonical:
            return None
        url = UNPAYWALL.format(doi=canonical)
        try:
            data = await self.client.get_json(
                url,
                provider="unpaywall",
                requests_per_second=8,
                params={"email": email},
            )
        except HttpError as exc:
            logger.info("Unpaywall lookup failed for %s: %s", canonical, exc)
            return None
        locations = [data.get("best_oa_location") or {}]
        locations.extend(loc for loc in (data.get("oa_locations") or []) if isinstance(loc, dict))
        candidates: list[tuple[int, str, str | None]] = []
        license_ = None
        for loc in locations:
            candidate = loc.get("url_for_pdf")
            license_ = license_ or loc.get("license")
            if not is_direct_pdf_url(candidate, prefer_https=False):
                continue
            candidates.append((oa_url_priority(candidate), candidate, loc.get("license")))
        candidates.sort(key=lambda item: item[0])
        is_oa = bool(data.get("is_oa"))
        if not candidates:
            return (None, license_, is_oa)
        if collect_all:
            return [item[1] for item in candidates], candidates[0][2] or license_, is_oa
        best = candidates[0]
        return best[1], best[2] or license_, is_oa


def _usable_pdf(url: str | None) -> bool:
    return is_direct_pdf_url(url, prefer_https=False)

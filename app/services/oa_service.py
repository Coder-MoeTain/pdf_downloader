"""Detect legally available open-access PDFs. Never bypasses paywalls."""

from __future__ import annotations

from app.config import AppConfig, load_config
from app.models.paper import PaperRecord, PaperStatus
from app.utils.doi import normalize_doi
from app.utils.http import AsyncHttpClient, HttpError
from app.utils.logger import get_logger
from app.utils.security import is_safe_url

logger = get_logger("app.oa")

UNPAYWALL = "https://api.unpaywall.org/v2/{doi}"


class OpenAccessService:
    def __init__(self, client: AsyncHttpClient, config: AppConfig | None = None) -> None:
        self.client = client
        self.config = config or load_config()

    async def resolve(self, paper: PaperRecord) -> PaperRecord:
        if paper.arxiv_id and not paper.pdf_url:
            paper.pdf_url = f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf"
            paper.open_access = True
        if paper.pmcid and not paper.pdf_url:
            pmc = paper.pmcid if str(paper.pmcid).upper().startswith("PMC") else f"PMC{paper.pmcid}"
            paper.pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc}/pdf/"
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

    async def _unpaywall(self, doi: str) -> tuple[str | None, str | None, bool] | None:
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
        best = data.get("best_oa_location") or {}
        pdf = best.get("url_for_pdf")
        license_ = best.get("license")
        is_oa = bool(data.get("is_oa"))
        return pdf, license_, is_oa


def _usable_pdf(url: str | None) -> bool:
    return bool(url) and is_safe_url(url)

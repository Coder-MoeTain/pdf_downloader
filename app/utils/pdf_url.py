"""Distinguish real PDF file URLs from publisher landing pages and DOI resolvers."""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from app.utils.security import is_safe_url

DOI_HOSTS = frozenset({"doi.org", "dx.doi.org", "www.doi.org"})
LANDING_HOSTS = frozenset(
    {
        "linkinghub.elsevier.com",
        "sciencedirect.com",
        "www.sciencedirect.com",
        "ssrn.com",
        "www.ssrn.com",
        "papers.ssrn.com",
    }
)
PDF_HINTS = (
    ".pdf",
    "/pdf/",
    "/pdf?",
    "/pdf&",
    "/download",
    "type=printable",
    "type=pdf",
    "format=pdf",
    "mimetype=pdf",
    "script=sci_pdf",
    "servlets/purl",
    "/stamp/stamp.jsp",
    "/stamppdf/",
    "/ielx",
    "/content/pdf/",
    "/doi/pdf",
    "/doi/epdf",
    "accept=application/pdf",
    "article/file",
)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def is_doi_resolver_url(url: str | None) -> bool:
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    return host in DOI_HOSTS or host.endswith(".doi.org")


def looks_like_pdf_path(url: str | None) -> bool:
    if not url:
        return False
    lower = unquote(url).lower()
    return any(hint in lower for hint in PDF_HINTS)


def is_direct_pdf_url(url: str | None, *, prefer_https: bool = True) -> bool:
    """True only when the URL itself is likely a PDF file, not an HTML article page."""
    if not url or not is_safe_url(url, prefer_https=prefer_https):
        return False
    if is_doi_resolver_url(url):
        return False
    host = _host(url)
    path = unquote(urlparse(url).path or "").lower()
    if host in LANDING_HOSTS and not looks_like_pdf_path(url):
        return False
    if "ieeexplore.ieee.org" in host and not looks_like_pdf_path(url):
        return False
    if looks_like_pdf_path(url):
        return True
    if "ncbi.nlm.nih.gov" in host and "/pdf" in path:
        return True
    if "europepmc.org" in host and "pdf" in unquote(url).lower():
        return True
    if "openreview.net" in host and "/pdf" in path:
        return True
    return False

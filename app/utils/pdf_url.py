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
# These look like PDF links but need publisher login / cookies and usually 404/403 anonymously.
GATED_PDF_HOSTS = frozenset(
    {
        "ieeexplore.ieee.org",
        "ieee.org",
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
    "/doi/pdfdirect",
    "accept=application/pdf",
    "article/file",
)

# Prefer repository / true-OA hosts when Unpaywall offers several locations.
PREFERRED_OA_HOST_SUFFIXES = (
    "arxiv.org",
    "biorxiv.org",
    "medrxiv.org",
    "chemrxiv.org",
    "ncbi.nlm.nih.gov",
    "europepmc.org",
    "pmc.ncbi.nlm.nih.gov",
    "zenodo.org",
    "hal.science",
    "archives-ouvertes.fr",
    "plos.org",
    "plosone.org",
    "peerj.com",
    "frontiersin.org",
    "mdpi.com",
    "springeropen.com",
    "biomedcentral.com",
    "nature.com",
    "osti.gov",
    "nasa.gov",
    "openreview.net",
    "osf.io",
    "figshare.com",
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


def is_gated_publisher_pdf(url: str | None) -> bool:
    """True for publisher CDN/PDF links that typically fail without institutional login."""
    if not url:
        return False
    host = _host(url)
    lower = unquote(url).lower()
    if host in GATED_PDF_HOSTS or host.endswith(".ieee.org"):
        return True
    # Wiley "pdfdirect" almost always 403s for anonymous bots.
    if "wiley.com" in host and ("pdfdirect" in lower or "/doi/pdf" in lower):
        return True
    # ACM PDF endpoints often require a session cookie.
    if host in {"dl.acm.org", "acm.org"} and "/doi/pdf" in lower:
        return True
    return False


def oa_url_priority(url: str | None) -> int:
    """Lower is better. Gated publisher URLs rank last."""
    if not url:
        return 10_000
    if is_gated_publisher_pdf(url):
        return 9_000
    host = _host(url)
    for index, suffix in enumerate(PREFERRED_OA_HOST_SUFFIXES):
        if host == suffix or host.endswith("." + suffix):
            return index
    return 500


def is_direct_pdf_url(url: str | None, *, prefer_https: bool = True) -> bool:
    """True only when the URL itself is likely a PDF file, not an HTML article page."""
    if not url or not is_safe_url(url, prefer_https=prefer_https):
        return False
    if is_doi_resolver_url(url):
        return False
    if is_gated_publisher_pdf(url):
        return False
    host = _host(url)
    path = unquote(urlparse(url).path or "").lower()
    if host in LANDING_HOSTS and not looks_like_pdf_path(url):
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

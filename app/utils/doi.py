"""DOI parsing and canonicalization."""

from __future__ import annotations

import re

PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
DOI_PATTERN = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)
TRAILING_PUNCT = re.compile(r"[.,;:]+$")


def normalize_doi(value: str | None) -> str | None:
    """Return canonical DOI `10.xxxx/....` or None if the value is not a DOI."""
    if not value:
        return None
    text = PREFIX.sub("", str(value).strip())
    text = text.strip().strip("<>")
    match = DOI_PATTERN.search(text)
    if match:
        candidate = match.group(1)
    elif text.lower().startswith("10."):
        candidate = text
    else:
        return None
    candidate = TRAILING_PUNCT.sub("", candidate.strip().rstrip(")"))
    candidate = candidate.replace(" ", "")
    if not candidate.lower().startswith("10."):
        return None
    if "/" not in candidate:
        return None
    return candidate.lower()


def extract_doi(text: str | None) -> str | None:
    if not text:
        return None
    match = DOI_PATTERN.search(str(text))
    if not match:
        return None
    return normalize_doi(match.group(1))


def doi_url(doi: str | None) -> str | None:
    canonical = normalize_doi(doi)
    if not canonical:
        return None
    return f"https://doi.org/{canonical}"

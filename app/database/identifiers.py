"""Canonical identifier normalization for papers."""

from __future__ import annotations

import re

from app.utils.doi import normalize_doi

IDENTIFIER_SCHEMES = ("doi", "pmid", "pmcid", "arxiv", "openalex", "semantic_scholar")

_ARXIV = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)$", re.I)
_PMID = re.compile(r"^\d{1,12}$")
_PMCID = re.compile(r"^(?:pmc)?(\d+)$", re.I)
_OPENALEX = re.compile(r"^(?:https?://openalex\.org/)?(w\d+)$", re.I)
_S2 = re.compile(r"^[0-9a-f]{40}$", re.I)


def normalize_identifier(scheme: str, value: str | None) -> str | None:
    """Return a comparable identifier or None if the value is unusable."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    key = (scheme or "").strip().lower()
    if key == "doi":
        return normalize_doi(text)
    if key == "pmid":
        digits = text.lower().removeprefix("pmid:").strip()
        return digits if _PMID.match(digits) else None
    if key == "pmcid":
        match = _PMCID.match(text.replace(" ", ""))
        return f"pmc{match.group(1)}" if match else None
    if key == "arxiv":
        text = text.strip().removeprefix("https://arxiv.org/abs/").removeprefix("http://arxiv.org/abs/")
        match = _ARXIV.match(text.replace("arxiv.org/pdf/", "").split(".pdf")[0])
        return match.group(1).lower() if match else text.lower() or None
    if key == "openalex":
        match = _OPENALEX.match(text.replace(" ", ""))
        return match.group(1).lower() if match else None
    if key in {"semantic_scholar", "s2"}:
        cleaned = text.strip().lower()
        return cleaned if _S2.match(cleaned) or cleaned else None
    return text.lower() or None


def record_identifiers(record) -> list[tuple[str, str, str]]:
    """Return (scheme, raw, normalized) triples for a PaperRecord."""
    pairs = (
        ("doi", getattr(record, "doi", None)),
        ("pmid", getattr(record, "pmid", None)),
        ("pmcid", getattr(record, "pmcid", None)),
        ("arxiv", getattr(record, "arxiv_id", None)),
        ("openalex", getattr(record, "openalex_id", None)),
        ("semantic_scholar", getattr(record, "semantic_scholar_id", None)),
    )
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for scheme, raw in pairs:
        if not raw:
            continue
        normalized = normalize_identifier(scheme, str(raw))
        if not normalized:
            continue
        key = (scheme, normalized)
        if key in seen:
            continue
        seen.add(key)
        out.append((scheme, str(raw).strip(), normalized))
    return out

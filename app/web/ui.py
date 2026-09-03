"""Shared UI helpers for status labels and navigation."""

from __future__ import annotations

from math import ceil
from urllib.parse import urlencode

STATUS_META: dict[str, dict[str, str]] = {
    "DOWNLOADED": {"label": "Downloaded", "tone": "success"},
    "OA_AVAILABLE": {"label": "Open access", "tone": "info"},
    "DOWNLOADING": {"label": "Downloading", "tone": "primary"},
    "PAYWALLED": {"label": "Paywalled", "tone": "warning"},
    "FAILED": {"label": "Failed", "tone": "danger"},
    "SKIPPED": {"label": "Skipped", "tone": "secondary"},
    "NO_PDF": {"label": "No PDF", "tone": "secondary"},
    "FOUND": {"label": "Found", "tone": "secondary"},
    "DUPLICATE": {"label": "Duplicate", "tone": "secondary"},
}


def status_meta(code: str | None) -> dict[str, str]:
    if not code:
        return {"label": "Unknown", "tone": "secondary"}
    return STATUS_META.get(code, {"label": str(code).replace("_", " ").title(), "tone": "secondary"})


def share(count: int, total: int) -> float:
    if not total:
        return 0.0
    return round((count / total) * 100, 1)


def active_page(path: str) -> str:
    mapping = (
        ("/search", "search"),
        ("/library", "library"),
        ("/downloads", "downloads"),
        ("/sources", "sources"),
        ("/statistics", "statistics"),
        ("/settings", "settings"),
        ("/account", "account"),
        ("/login", "login"),
        ("/papers", "library"),
    )
    for prefix, name in mapping:
        if path.startswith(prefix):
            return name
    return "dashboard"


PAGE_SIZES = (10, 25, 50)
DEFAULT_PAGE_SIZE = 25
DEFAULT_SORT = "relevance"
SORT_OPTIONS = (
    ("relevance", "Relevance"),
    ("newest", "Newest"),
    ("oldest", "Oldest"),
    ("citations", "Citations"),
    ("rating", "Your rating"),
)


def source_label(slug: str | None) -> str:
    if not slug:
        return ""
    from app.database.source_catalog import BUILTIN_SOURCES

    labels = {str(item["slug"]): str(item["display_name"]) for item in BUILTIN_SOURCES}
    return labels.get(slug, slug.replace("_", " ").title())


def clamp_page_size(value) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return size if size in PAGE_SIZES else DEFAULT_PAGE_SIZE


def pagination_items(page: int, total_pages: int, neighbors: int = 1) -> list[int | None]:
    if total_pages <= 0:
        return []
    if total_pages <= 7:
        return list(range(1, total_pages + 1))
    selected = {1, total_pages, page}
    for number in range(page - neighbors, page + neighbors + 1):
        if 1 <= number <= total_pages:
            selected.add(number)
    ordered = sorted(selected)
    items: list[int | None] = []
    previous = 0
    for number in ordered:
        if previous and number - previous > 1:
            items.append(None)
        items.append(number)
        previous = number
    return items


def pagination_spec(page: int, total: int, per_page: int) -> dict:
    per_page = max(int(per_page), 1)
    total_pages = max(1, ceil(total / per_page)) if total else 1
    page = max(1, min(int(page), total_pages))
    start = 0 if not total else (page - 1) * per_page + 1
    end = min(page * per_page, total)
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "start": start,
        "end": end,
        "pages": pagination_items(page, total_pages if total else 0),
        "has_prev": bool(total) and page > 1,
        "has_next": bool(total) and page < total_pages,
    }


def library_href(current: dict | None = None, **overrides) -> str:
    """Build a /library URL, omitting default filter values."""
    merged = {**(current or {}), **overrides}
    pairs: list[tuple[str, str]] = []
    query = str(merged.get("q") or "").strip()
    if query:
        pairs.append(("q", query))
    status = str(merged.get("status") or "").strip()
    if status:
        pairs.append(("status", status))
    if merged.get("pdf"):
        pairs.append(("pdf", "1"))
    try:
        min_rating = int(merged.get("min_rating") or 0)
    except (TypeError, ValueError):
        min_rating = 0
    if min_rating:
        pairs.append(("min_rating", str(min_rating)))
    if merged.get("latest"):
        pairs.append(("latest", "1"))
    category = str(merged.get("category") or "").strip()
    if category:
        pairs.append(("category", category))
    try:
        year = int(merged.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    if year:
        pairs.append(("year", str(year)))
    source = str(merged.get("source") or "").strip()
    if source:
        pairs.append(("source", source))
    journal = str(merged.get("journal") or "").strip()
    if journal:
        pairs.append(("journal", journal))
    sort = str(merged.get("sort") or DEFAULT_SORT).strip() or DEFAULT_SORT
    if sort != DEFAULT_SORT:
        pairs.append(("sort", sort))
    per_page = clamp_page_size(merged.get("per_page") or DEFAULT_PAGE_SIZE)
    if per_page != DEFAULT_PAGE_SIZE:
        pairs.append(("per_page", str(per_page)))
    try:
        page = int(merged.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    if page > 1:
        pairs.append(("page", str(page)))
    query_string = urlencode(pairs)
    return f"/library?{query_string}" if query_string else "/library"

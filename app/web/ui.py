"""Shared UI helpers for status labels and navigation."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from typing import Annotated, Any
from urllib.parse import urlencode, urlparse

from pydantic import BeforeValidator

from app.utils.time import utc_now

NEW_DOWNLOAD_AGE = timedelta(hours=24)

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


def paper_authors_line(paper, limit: int | None = 6) -> str:
    """Comma-separated author names for library and abstract preview."""
    links = sorted(getattr(paper, "authors", None) or [], key=lambda item: item.position or 0)
    names = [
        link.author.name
        for link in links
        if getattr(link, "author", None) and link.author.name
    ]
    if not names:
        return ""
    if limit is not None and len(names) > limit:
        return ", ".join(names[:limit]) + f" +{len(names) - limit}"
    return ", ".join(names)


def paper_categories(paper) -> list[str]:
    """Distinct research-field and keyword tags for a paper."""
    from app.database.repository import split_tags

    seen: set[str] = set()
    tags: list[str] = []
    for raw in (getattr(paper, "research_fields", None), getattr(paper, "keywords", None)):
        for tag in split_tags(raw):
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                tags.append(tag)
    return tags


def paper_downloader_name(paper) -> str:
    """Display name of the account that saved the PDF, if recorded."""
    rows = sorted(getattr(paper, "downloads", None) or [], key=lambda item: item.id or 0, reverse=True)
    for row in rows:
        user = getattr(row, "downloaded_by", None)
        if not user:
            continue
        label = (getattr(user, "name", None) or getattr(user, "email", None) or "").strip()
        if label:
            return label
    return ""


def paper_record_date(paper):
    """When the PDF was saved, or when the paper was added to the library."""
    rows = sorted(getattr(paper, "downloads", None) or [], key=lambda item: item.id or 0, reverse=True)
    for row in rows:
        stamp = getattr(row, "downloaded_at", None)
        if stamp:
            return stamp
    return getattr(paper, "created_at", None)


def download_record_date(row):
    """When this download finished, or when the paper was stored."""
    stamp = getattr(row, "downloaded_at", None)
    if stamp:
        return stamp
    paper = getattr(row, "paper", None)
    if paper is not None:
        return getattr(paper, "created_at", None)
    return None


def paper_abstract_meta(paper) -> str:
    """Year, venue, and authors shown under the abstract preview title."""
    parts = []
    authors = paper_authors_line(paper)
    if authors:
        parts.append(authors)
    if getattr(paper, "publication_year", None):
        parts.append(str(paper.publication_year))
    venue = getattr(paper, "journal", None) or getattr(paper, "publisher", None)
    if venue:
        parts.append(venue)
    return " · ".join(parts)


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
        ("/crawler", "crawler"),
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


def source_homepage(slug: str | None, homepage_url: str | None = None) -> str:
    if homepage_url:
        return homepage_url.strip()
    if not slug:
        return ""
    from app.database.source_catalog import BUILTIN_SOURCES

    for item in BUILTIN_SOURCES:
        if str(item["slug"]) == slug:
            return str(item.get("homepage_url") or "")
    return ""


def source_logo_url(slug: str | None, homepage_url: str | None = None) -> str:
    url = source_homepage(slug, homepage_url)
    if url:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        domain = parsed.netloc or parsed.path.split("/")[0]
        if domain:
            return f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
    return "/static/favicon.svg"


def _blank_query_int(default: int):
    def coerce(value: Any) -> Any:
        if value is None or value == "":
            return default
        return value

    return Annotated[int, BeforeValidator(coerce)]


QueryInt = _blank_query_int(0)
QueryPage = _blank_query_int(1)


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
    try:
        user = int(merged.get("user") or 0)
    except (TypeError, ValueError):
        user = 0
    if user:
        pairs.append(("user", str(user)))
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


def downloads_href(current: dict | None = None, **overrides) -> str:
    """Build a /downloads URL, omitting default filter values."""
    merged = {**(current or {}), **overrides}
    pairs: list[tuple[str, str]] = []
    query = str(merged.get("q") or "").strip()
    if query:
        pairs.append(("q", query))
    status = str(merged.get("status") or "").strip()
    if status:
        pairs.append(("status", status))
    try:
        user = int(merged.get("user") or 0)
    except (TypeError, ValueError):
        user = 0
    if user:
        pairs.append(("user", str(user)))
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
    return f"/downloads?{query_string}" if query_string else "/downloads"


def sources_href(current: dict | None = None, **overrides) -> str:
    """Build a /sources URL, omitting default filter values."""
    merged = {**(current or {}), **overrides}
    pairs: list[tuple[str, str]] = []
    query = str(merged.get("q") or "").strip()
    if query:
        pairs.append(("q", query))
    status = str(merged.get("status") or "").strip()
    if status:
        pairs.append(("status", status))
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
    return f"/sources?{query_string}" if query_string else "/sources"


SOURCE_STATUS_ORDER = ("available", "disabled", "needs_key", "inactive")
SOURCE_STATUS_META = {
    "available": {"label": "Available", "tone": "success"},
    "disabled": {"label": "Disabled", "tone": "secondary"},
    "needs_key": {"label": "Needs key", "tone": "warning"},
    "inactive": {"label": "Inactive", "tone": "secondary"},
}


def source_row_status(item: dict) -> str:
    if item.get("available"):
        return "available"
    if not item.get("enabled"):
        return "disabled"
    if item.get("requires_key") and not item.get("has_key"):
        return "needs_key"
    return "inactive"


def source_matches(item: dict, q: str = "", status: str = "") -> bool:
    if status and source_row_status(item) != status:
        return False
    needle = (q or "").strip().lower()
    if not needle:
        return True
    hay = " ".join(
        str(item.get(key) or "") for key in ("display_name", "slug", "kind", "description")
    ).lower()
    return needle in hay


def ordered_source_status_counts(counts: dict) -> list[dict]:
    items: list[dict] = []
    for code in SOURCE_STATUS_ORDER:
        n = counts.get(code) or 0
        if n:
            items.append({"code": code, "count": n, **SOURCE_STATUS_META[code]})
    return items


DOWNLOAD_STATUS_ORDER = (
    "DOWNLOADING",
    "DOWNLOADED",
    "FAILED",
    "OA_AVAILABLE",
    "FOUND",
    "DUPLICATE",
    "SKIPPED",
    "NO_PDF",
    "PAYWALLED",
)


def ordered_status_counts(counts: dict) -> list[dict]:
    """Status chips in a stable, useful order."""
    items: list[dict] = []
    used: set[str] = set()
    for code in DOWNLOAD_STATUS_ORDER:
        n = counts.get(code) or 0
        if n:
            items.append({"code": code, "count": n, **status_meta(code)})
            used.add(code)
    for code, n in counts.items():
        if code not in used and n:
            items.append({"code": code, "count": n, **status_meta(code)})
    return items


def library_status_panel(facets: dict) -> dict:
    """KPI cards and status-mix rows for the library page."""
    counts = dict(facets.get("status_counts") or {})
    total = int(facets.get("visible_total") or sum(counts.values()) or 0)
    downloadable = int(facets.get("downloadable") or 0)
    paywalled = int(facets.get("paywalled") or 0)
    statuses = [{**item, "pct": share(item["count"], total)} for item in ordered_status_counts(counts)]
    return {
        "visible_total": total,
        "downloadable": downloadable,
        "downloaded": int(counts.get("DOWNLOADED") or 0),
        "open_access": int(counts.get("OA_AVAILABLE") or 0),
        "paywalled": paywalled,
        "statuses": statuses,
        "has_stats": bool(total or paywalled or statuses),
    }


def download_actor_name(row) -> str:
    """Name of the account that saved this download row."""
    user = getattr(row, "downloaded_by", None)
    if not user:
        return ""
    return (getattr(user, "name", None) or getattr(user, "email", None) or "").strip()


def is_new_download(row, *, now: datetime | None = None) -> bool:
    """True when a saved PDF was downloaded within the last 24 hours."""
    status = getattr(row, "status", None)
    if status not in {"DOWNLOADED", "DUPLICATE"}:
        return False
    if not getattr(row, "local_path", None):
        return False
    stamp = getattr(row, "downloaded_at", None)
    if not isinstance(stamp, datetime):
        return False
    current = now or utc_now()
    if stamp.tzinfo is not None:
        stamp = stamp.replace(tzinfo=None)
    if current.tzinfo is not None:
        current = current.replace(tzinfo=None)
    return stamp >= current - NEW_DOWNLOAD_AGE

"""Shared helpers for paginated source crawls."""

from __future__ import annotations

from app.models.crawl import BrowsePage, CrawlFilters
from app.models.paper import PaperRecord
from app.models.search import SearchFilters, SortMode


def search_from_crawl(filters: CrawlFilters, *, page_size: int | None = None) -> SearchFilters:
    return SearchFilters(
        query=filters.query.strip(),
        year_from=filters.year_from,
        year_to=filters.year_to,
        open_access_only=filters.open_access_only,
        max_results=page_size or min(filters.page_size, 100),
        sort=SortMode.NEWEST,
    )


def page_offset(cursor: str | None, page_size: int) -> tuple[int, int]:
    page_num = max(1, int(cursor or "1"))
    return page_num, (page_num - 1) * page_size


def finish_page(
    records: list[PaperRecord | None],
    *,
    page_num: int,
    page_size: int,
    total: int | None = None,
    next_cursor: str | None = None,
    has_more: bool | None = None,
) -> BrowsePage:
    cleaned = [row for row in records if row and row.title]
    if has_more is None:
        has_more = len(cleaned) >= page_size
    if has_more and not next_cursor:
        next_cursor = str(page_num + 1)
    if not has_more:
        next_cursor = None
    return BrowsePage(
        records=cleaned,
        next_cursor=next_cursor,
        has_more=has_more,
        page_number=page_num,
        total_results=total,
    )


def crawl_query(filters: CrawlFilters, *, fallback: str) -> str:
    return filters.query.strip() or fallback

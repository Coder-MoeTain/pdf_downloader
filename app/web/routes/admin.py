"""Academic source catalog administration."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.database.settings_repository import (
    get_academic_source,
    source_to_dict,
)
from app.database.settings_store import store_status
from app.web.dependencies import (
    _ctx,
    _source_rows,
    templates,
)
from app.web.ui import (
    DEFAULT_PAGE_SIZE,
    PAGE_SIZES,
    QueryInt,
    QueryPage,
    clamp_page_size,
    ordered_source_status_counts,
    pagination_spec,
    source_matches,
    source_row_status,
)

router = APIRouter()


@router.get("/sources", response_class=HTMLResponse)
def sources_page(
    request: Request,
    status: str = "",
    q: str = "",
    page: QueryPage = 1,
    per_page: QueryInt = DEFAULT_PAGE_SIZE,
):
    status = status.strip()
    q = q.strip()
    per_page = clamp_page_size(per_page)
    all_sources = _source_rows()
    searched = [item for item in all_sources if source_matches(item, q=q)]
    counts: dict[str, int] = {}
    for item in searched:
        code = source_row_status(item)
        counts[code] = counts.get(code, 0) + 1
    rows = [item for item in searched if source_matches(item, status=status)]
    pager = pagination_spec(max(page, 1), len(rows), per_page)
    start = (pager["page"] - 1) * per_page
    page_rows = rows[start : start + per_page]
    filters_state = {"status": status, "q": q, "per_page": per_page}
    return templates.TemplateResponse(
        request,
        "sources.html",
        _ctx(
            request,
            sources=page_rows,
            store=store_status().as_dict(),
            counts=counts,
            status_chips=ordered_source_status_counts(counts),
            status=status,
            q=q,
            per_page=per_page,
            pager=pager,
            filters=filters_state,
            page_sizes=PAGE_SIZES,
            has_filters=bool(status or q),
            source_stats={
                "total": len(all_sources),
                "available": sum(1 for item in all_sources if item["available"]),
            },
        ),
    )


@router.get("/api/sources/{source_id}")
def api_source_get(source_id: int):
    row = get_academic_source(source_id)
    if row is None:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    return {"ok": True, "source": source_to_dict(row)}

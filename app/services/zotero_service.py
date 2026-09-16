"""Optional Zotero Web API client. Core export still works when this is unconfigured."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import load_config
from app.exceptions import CyberScholarError
from app.security.ssrf import resolve_and_validate_host, validate_outbound_url
from app.services.citation import paper_citations
from app.utils.logger import get_logger

logger = get_logger("app.zotero")
ZOTERO_HOST = "api.zotero.org"


class ZoteroNotConfigured(CyberScholarError):
    status_code = 400
    public_message = "Zotero is optional. Set ZOTERO_API_KEY and ZOTERO_USER_ID to push items."


def is_configured() -> bool:
    env = load_config().env
    return bool((env.zotero_api_key or "").strip() and (env.zotero_user_id or "").strip())


def items_from_papers(papers: list[Any]) -> list[dict[str, Any]]:
    items = []
    for paper in papers:
        names = _author_parts(paper)
        creators = [{"creatorType": "author", "lastName": last, "firstName": given} for last, given in names]
        item = {
            "itemType": "journalArticle" if getattr(paper, "journal", None) else "report",
            "title": getattr(paper, "title", None) or "Untitled",
            "creators": creators,
            "date": str(getattr(paper, "publication_year", "") or ""),
            "publicationTitle": getattr(paper, "journal", None) or getattr(paper, "conference", None) or "",
            "DOI": getattr(paper, "doi", None) or "",
            "url": getattr(paper, "url", None) or "",
            "abstractNote": (getattr(paper, "abstract", None) or "")[:5000],
        }
        extra = []
        if getattr(paper, "pmid", None):
            extra.append(f"PMID: {paper.pmid}")
        if getattr(paper, "arxiv_id", None):
            extra.append(f"arXiv: {paper.arxiv_id}")
        if extra:
            item["extra"] = "\n".join(extra)
        items.append(item)
    return items


def csl_items(papers: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for paper in papers:
        raw = paper_citations(paper).get("csl_json") or "{}"
        try:
            import json

            payload = json.loads(raw)
        except Exception:
            payload = {"title": getattr(paper, "title", None) or "Untitled"}
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


async def push_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not is_configured():
        raise ZoteroNotConfigured()
    env = load_config().env
    library = "groups" if (env.zotero_library_type or "user").strip().lower() == "group" else "users"
    path = f"/{library}/{env.zotero_user_id.strip()}/items"
    url = f"https://{ZOTERO_HOST}{path}"
    checked = validate_outbound_url(url, prefer_https=True, resolve_dns=False)
    host = urlparse(checked).hostname or ""
    resolve_and_validate_host(host)
    headers = {
        "Zotero-API-Key": env.zotero_api_key.strip(),
        "Zotero-API-Version": "3",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        response = await client.post(checked, json=items, headers=headers)
    if response.status_code >= 400:
        logger.warning("Zotero push failed: %s %s", response.status_code, response.text[:300])
        raise CyberScholarError(f"Zotero API returned HTTP {response.status_code}.")
    try:
        payload = response.json()
    except Exception:
        payload = {"ok": True}
    return {"ok": True, "created": len(items), "response": payload}


def _author_parts(paper: Any) -> list[tuple[str, str]]:
    authors = list(getattr(paper, "authors", None) or [])
    names: list[str] = []
    if authors and hasattr(authors[0], "author"):
        links = sorted(authors, key=lambda item: getattr(item, "position", 0) or 0)
        names = [link.author.name.strip() for link in links if getattr(link, "author", None) and link.author.name]
    elif authors and hasattr(authors[0], "name"):
        names = [item.name.strip() for item in authors if getattr(item, "name", None)]
    parts = []
    for name in names:
        if "," in name:
            last, _, given = name.partition(",")
            parts.append((last.strip(), given.strip()))
        else:
            bits = name.split()
            parts.append((bits[-1], " ".join(bits[:-1])) if bits else (name, ""))
    return parts

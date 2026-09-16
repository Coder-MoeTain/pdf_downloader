"""Fetch and cache Call for Papers from WikiCFP."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx
from defusedxml import ElementTree as ET

from app.database.connection import session_scope
from app.database.repository import latest_cfp_fetch_at, upsert_cfp_call
from app.exceptions import UnsafeUrlError
from app.security.ssrf import validate_outbound_url
from app.utils.logger import get_logger
from app.utils.time import utc_now

logger = get_logger("app.cfp")

WIKICFP_HTTP_HOSTS = {"www.wikicfp.com", "wikicfp.com"}
WIKICFP_RSS = "http://www.wikicfp.com/cfp/rss"
CFP_KEYWORDS = (
    "artificial intelligence",
    "cybersecurity",
    "machine learning",
    "satellite",
    "cryptography",
    "edge computing",
)
REFRESH_TTL = timedelta(hours=6)
DETAIL_LIMIT = 24
DETAIL_LIMIT_MAX = 200
REQUEST_TIMEOUT = 12.0
ENRICH_CONCURRENCY = 6
UPSERT_CHUNK = 10
STALE_REFRESH_SECONDS = 180.0
USER_AGENT = "CyberScholarCFP/1.2 (+https://research.msa.gov.mm)"

_refresh_lock = threading.RLock()
_refreshing = False
_refresh_started_at = 0.0
_last_refresh_result: dict[str, Any] = {"status": "idle", "message": ""}

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def parse_cfp_date(value: str | None) -> datetime | None:
    """Parse common WikiCFP date strings into a naive UTC datetime at midnight."""
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text or text.upper() in {"N/A", "TBD", "TBA", "NONE"}:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt)
        except ValueError:
            pass
    m = re.match(
        r"^(?P<a>[A-Za-z]{3,9})\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>\d{4})$",
        text,
        re.I,
    )
    if m:
        month = _MONTHS.get(m.group("a").lower())
        if month:
            return datetime(int(m.group("y")), month, int(m.group("d")))
    m = re.match(
        r"^(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?P<a>[A-Za-z]{3,9})\.?,?\s+(?P<y>\d{4})$",
        text,
        re.I,
    )
    if m:
        month = _MONTHS.get(m.group("a").lower())
        if month:
            return datetime(int(m.group("y")), month, int(m.group("d")))
    m = re.match(r"^[A-Za-z]{3},\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", text)
    if m:
        month = _MONTHS.get(m.group(2).lower())
        if month:
            return datetime(int(m.group(3)), month, int(m.group(1)))
    return None


def strip_html(value: str | None) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


def external_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    query = parsed.query or ""
    match = re.search(r"eventid=(\d+)", query, re.I)
    if match:
        return f"wikicfp:{match.group(1)}"
    path = (parsed.path or "").rstrip("/")
    if path:
        slug = path.split("/")[-1] or path
        if slug and slug not in {"cfp", "show", "servlet"}:
            return f"wikicfp:{slug}"[:255]
    digest = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"wikicfp:{digest}"


def favicon_url_for(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"http://{url}")
    domain = parsed.netloc or "www.wikicfp.com"
    if domain:
        return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
    return "/static/favicon.svg"


# Curated research/tech covers (Unsplash). WikiCFP pages rarely expose posters.
_COVER_PHOTOS = (
    "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=960&h=540&q=80",
    "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=960&h=540&q=80",
    "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?auto=format&fit=crop&w=960&h=540&q=80",
    "https://images.unsplash.com/photo-1677442136019-21780ecad995?auto=format&fit=crop&w=960&h=540&q=80",
    "https://images.unsplash.com/photo-1486312338219-ce68d2c6f44d?auto=format&fit=crop&w=960&h=540&q=80",
    "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=960&h=540&q=80",
    "https://images.unsplash.com/photo-1635070041078-e363dbe005cb?auto=format&fit=crop&w=960&h=540&q=80",
    "https://images.unsplash.com/photo-1446776811953-b23d57bd21aa?auto=format&fit=crop&w=960&h=540&q=80",
    "https://images.unsplash.com/photo-1504639725590-34d0984388bd?auto=format&fit=crop&w=960&h=540&q=80",
    "https://images.unsplash.com/photo-1558494949-ef010cbdcc31?auto=format&fit=crop&w=960&h=540&q=80",
    "https://images.unsplash.com/photo-1581091226825-a6a2a5aee158?auto=format&fit=crop&w=960&h=540&q=80",
    "https://images.unsplash.com/photo-1532094349884-543bc11b234d?auto=format&fit=crop&w=960&h=540&q=80",
)

_CATEGORY_COVER_INDEX = {
    "artificial intelligence": 3,
    "machine learning": 3,
    "cybersecurity": 2,
    "cryptography": 5,
    "satellite": 7,
    "edge computing": 9,
}


def is_generic_cfp_image(url: str | None) -> bool:
    text = (url or "").strip().lower()
    if not text:
        return True
    return (
        "google.com/s2/favicons" in text
        or text.endswith("/static/favicon.svg")
        or text.endswith("favicon.svg")
        or "/cfp/images/wikicfp" in text
    )


def cover_image_url_for(external_id: str, categories: str | None = None) -> str:
    """Stable photo URL per call — WikiCFP itself has no conference posters."""
    cats = [c.strip().lower() for c in (categories or "").split(",") if c.strip()]
    for cat in cats:
        if cat in _CATEGORY_COVER_INDEX:
            base = _CATEGORY_COVER_INDEX[cat]
            break
    else:
        base = 0
    digest = hashlib.sha1(f"{external_id}|{categories or ''}".encode(), usedforsecurity=False).hexdigest()
    offset = int(digest[:6], 16) % len(_COVER_PHOTOS)
    return _COVER_PHOTOS[(base + offset) % len(_COVER_PHOTOS)]


def display_image_url(
    image_url: str | None,
    *,
    external_id: str = "",
    categories: str | None = None,
) -> str:
    if image_url and not is_generic_cfp_image(image_url):
        return image_url
    return cover_image_url_for(external_id or "cfp", categories)


def cfp_display_image(call: Any) -> str:
    return display_image_url(
        getattr(call, "image_url", None),
        external_id=str(getattr(call, "external_id", "") or ""),
        categories=getattr(call, "categories", None),
    )


def prefer_http_wikicfp(url: str) -> str:
    if url.startswith("https://www.wikicfp.com"):
        return "http://" + url[len("https://") :]
    if url.startswith("https://wikicfp.com"):
        return "http://" + url[len("https://") :]
    return url


def parse_rss_items(xml_text: str) -> list[dict[str, str]]:
    """Parse WikiCFP RSS into dicts with title/link/description/pubDate."""
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []
    items: list[dict[str, str]] = []
    for node in channel.findall("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not title or not link:
            continue
        items.append(
            {
                "title": title,
                "link": prefer_http_wikicfp(link),
                "description": node.findtext("description") or "",
                "pubDate": node.findtext("pubDate") or "",
            }
        )
    return items


def parse_event_range(text: str) -> tuple[datetime | None, datetime | None]:
    """Parse '[Nov 5, 2026 - Nov 5, 2026]' style ranges from RSS blurbs."""
    match = re.search(
        r"\[([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})\s*[-–]\s*([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})\]",
        text,
    )
    if not match:
        return None, None
    return parse_cfp_date(match.group(1)), parse_cfp_date(match.group(2))


def parse_location_from_blurb(text: str) -> str | None:
    match = re.search(r"\[([^\[\]]+)\]\s*\[[^\[\]]+\]\s*$", text.strip())
    if match:
        return match.group(1).strip()[:255]
    return None


def extract_og_image(html: str) -> str | None:
    patterns = (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return match.group(1).strip()
    return None


def extract_deadline_from_html(html: str) -> datetime | None:
    """Pull Submission Deadline from a WikiCFP event page."""
    text = strip_html(html)
    patterns = (
        r"Submission\s+Deadline\s*[:\-]?\s*([A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})",
        r"Paper\s+Submission\s*[:\-]?\s*([A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})",
        r"Deadline\s*[:\-]?\s*([A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})",
        r"Submission\s+Deadline\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})",
        r"Deadline\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            parsed = parse_cfp_date(match.group(1))
            if parsed:
                return parsed
    return None


def extract_location_from_html(html: str) -> str | None:
    text = strip_html(html)
    match = re.search(r"Where\s*[:\-]?\s*([A-Za-z0-9 ,./\-]{3,80})", text, re.I)
    if match:
        loc = match.group(1).strip()
        loc = re.split(r"\s+Submission\b|\s+When\b|\s+Categories\b", loc, maxsplit=1)[0].strip()
        return loc[:255] or None
    return None


def extract_website_from_html(html: str) -> str | None:
    """Extract the conference/event website from a WikiCFP event page."""
    match = re.search(
        r"Link:\s*<a[^>]+href=[\"'](https?://[^\"']+)[\"']",
        html,
        re.I,
    )
    if not match:
        match = re.search(
            r"Link:\s*(https?://[^\s<\"']+)",
            strip_html(html),
            re.I,
        )
    if not match:
        return None
    url = match.group(1).strip().rstrip(").,;")
    host = (urlparse(url).netloc or "").lower()
    if not host or "wikicfp.com" in host:
        return None
    skip = ("facebook.com", "twitter.com", "x.com", "linkedin.com", "creativecommons.org")
    if any(s in host for s in skip):
        return None
    return url[:2000]


def should_refresh(*, force: bool = False) -> bool:
    if force:
        return True
    try:
        with session_scope() as session:
            session.connection().exec_driver_sql("PRAGMA busy_timeout=1500")
            latest = latest_cfp_fetch_at(session)
    except Exception:
        return True
    if latest is None:
        return True
    age = utc_now() - latest
    return age >= REFRESH_TTL


def refresh_status() -> dict[str, Any]:
    _clear_stale_refresh()
    with _refresh_lock:
        return {
            "running": _refreshing,
            **dict(_last_refresh_result),
        }


def _set_result(**kwargs: Any) -> None:
    global _last_refresh_result
    with _refresh_lock:
        _last_refresh_result = {**_last_refresh_result, **kwargs}


def _clear_stale_refresh() -> None:
    """Unstick a hung WikiCFP worker so the UI can recover."""
    global _refreshing, _refresh_started_at
    with _refresh_lock:
        if not _refreshing:
            return
        if _refresh_started_at and (time.monotonic() - _refresh_started_at) > STALE_REFRESH_SECONDS:
            _refreshing = False
            _refresh_started_at = 0.0
            _last_refresh_result = {
                "status": "error",
                "message": "Previous WikiCFP refresh timed out. Try Refresh again.",
            }
            logger.warning("Cleared stale CFP refresh lock after %.0fs", STALE_REFRESH_SECONDS)


def _http_get_text(url: str, *, timeout: float = REQUEST_TIMEOUT) -> str:
    target = prefer_http_wikicfp(url)
    validate_outbound_url(target, allow_http_hosts=WIKICFP_HTTP_HOSTS)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml,text/html,*/*"}
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        response = client.get(target)
        response.raise_for_status()
        return response.text


def _fetch_rss_keyword(keyword: str) -> list[dict[str, str]]:
    url = f"{WIKICFP_RSS}?cat={quote_plus(keyword)}"
    try:
        text = _http_get_text(url)
    except (httpx.HTTPError, UnsafeUrlError, TimeoutError, OSError) as exc:
        logger.warning("WikiCFP RSS failed for %s: %s", keyword, exc)
        return []
    except Exception as exc:
        logger.warning("WikiCFP RSS error for %s: %s", keyword, exc)
        return []
    try:
        return parse_rss_items(text)
    except ET.ParseError as exc:
        logger.warning("WikiCFP RSS parse failed for %s: %s", keyword, exc)
        return []


def _enrich_event(link: str) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    try:
        html = _http_get_text(prefer_http_wikicfp(link))
    except Exception as exc:
        logger.warning("WikiCFP event page failed %s: %s", link, exc)
        return extra
    image = extract_og_image(html)
    if image:
        extra["image_url"] = image
    deadline = extract_deadline_from_html(html)
    if deadline:
        extra["deadline"] = deadline
    location = extract_location_from_html(html)
    if location:
        extra["location"] = location
    website = extract_website_from_html(html)
    if website:
        extra["website_url"] = website
    return extra


def _enrich_limit() -> int:
    try:
        from app.config import get_runtime_config

        configured = int(getattr(get_runtime_config(), "cfp_list_limit", DETAIL_LIMIT) or DETAIL_LIMIT)
    except Exception:
        configured = DETAIL_LIMIT
    # Enrich enough upcoming calls to cover the list (plus headroom for filtering).
    return max(DETAIL_LIMIT, min(DETAIL_LIMIT_MAX, max(configured * 2, configured + 20)))


def _apply_estimated_deadlines(seen: dict[str, dict[str, Any]], now: datetime) -> None:
    for payload in seen.values():
        if payload.get("deadline") or not payload.get("event_start"):
            continue
        est = payload["event_start"] - timedelta(days=45)
        if est > now:
            payload["deadline"] = est
            payload["deadline_verified"] = False
            payload["deadline_confidence"] = "estimated"
            payload["deadline_source"] = "wikicfp-estimated"


def _select_enrich_targets(
    seen: dict[str, dict[str, Any]],
    now: datetime,
    *,
    limit: int,
    within_days: int = 90,
) -> list[dict[str, Any]]:
    """Prefer calls that will appear on the CFP page (upcoming deadlines / events)."""
    end = now + timedelta(days=max(1, within_days))
    ranked: list[tuple[int, datetime, dict[str, Any]]] = []
    for payload in seen.values():
        deadline = payload.get("deadline")
        event_start = payload.get("event_start")
        if deadline is not None and now <= deadline <= end:
            ranked.append((0, deadline, payload))
        elif event_start is not None and now <= event_start <= end:
            ranked.append((1, event_start, payload))
    ranked.sort(key=lambda item: (item[0], item[1]))
    chosen = [payload for _, _, payload in ranked[: max(1, limit)]]
    if len(chosen) < limit:
        seen_ids = {id(p) for p in chosen}
        for payload in seen.values():
            if id(payload) in seen_ids:
                continue
            chosen.append(payload)
            if len(chosen) >= limit:
                break
    return chosen


def _merge_rss_items(keyword: str, items: list[dict[str, str]], seen: dict[str, dict[str, Any]], now: datetime) -> None:
    for item in items:
        link = prefer_http_wikicfp(item["link"])
        ext = external_id_from_url(link)
        if ext in seen:
            cats = seen[ext].get("categories") or ""
            if keyword not in cats:
                seen[ext]["categories"] = ", ".join(filter(None, [cats, keyword]))
            continue
        summary = strip_html(item.get("description"))
        event_start, event_end = parse_event_range(summary)
        location = parse_location_from_blurb(summary)
        seen[ext] = {
            "external_id": ext,
            "title": item["title"][:512],
            "summary": summary[:2000],
            "url": link,
            "website_url": None,
            "image_url": cover_image_url_for(ext, keyword),
            "deadline": None,
            "event_start": event_start,
            "event_end": event_end,
            "location": location,
            "categories": keyword,
            "source": "wikicfp",
            "deadline_source": "wikicfp",
            "deadline_verified": False,
            "deadline_confidence": "unknown",
            "source_url": link,
            "fetched_at": now,
        }


def refresh_cfps(*, force: bool = False, claim: bool = True) -> dict[str, int]:
    """Fetch WikiCFP RSS for configured keywords and upsert into the cache (sync)."""
    global _refreshing, _refresh_started_at
    if not should_refresh(force=force):
        _set_result(status="skipped", message="Cache is still fresh (refreshed within 6 hours).")
        return {"skipped": 1, "upserted": 0, "enriched": 0}
    if claim:
        with _refresh_lock:
            if _refreshing:
                _set_result(status="busy", message="A Call for Papers refresh is already running.")
                return {"skipped": 1, "upserted": 0, "enriched": 0}
            _refreshing = True
            _refresh_started_at = time.monotonic()
            _set_result(status="running", message="Refreshing from WikiCFP…")
    upserted = 0
    enriched = 0
    with_deadline = 0
    try:
        now = utc_now()
        seen: dict[str, dict[str, Any]] = {}
        _set_result(status="running", message="Fetching WikiCFP feeds…")
        with ThreadPoolExecutor(max_workers=len(CFP_KEYWORDS)) as pool:
            futures = {pool.submit(_fetch_rss_keyword, kw): kw for kw in CFP_KEYWORDS}
            for fut in as_completed(futures):
                keyword = futures[fut]
                try:
                    items = fut.result()
                except Exception as exc:
                    logger.warning("WikiCFP RSS worker failed for %s: %s", keyword, exc)
                    items = []
                _merge_rss_items(keyword, items, seen, now)

        _apply_estimated_deadlines(seen, now)
        to_enrich = _select_enrich_targets(seen, now, limit=_enrich_limit())
        if to_enrich:
            _set_result(status="running", message=f"Reading conference websites for {len(to_enrich)} calls…")
            with ThreadPoolExecutor(max_workers=ENRICH_CONCURRENCY) as pool:
                futures = {pool.submit(_enrich_event, row["url"]): row for row in to_enrich}
                for fut in as_completed(futures):
                    payload = futures[fut]
                    try:
                        extra = fut.result()
                    except Exception:
                        continue
                    if not extra:
                        continue
                    enriched += 1
                    if extra.get("deadline"):
                        payload["deadline"] = extra["deadline"]
                    if extra.get("image_url"):
                        payload["image_url"] = extra["image_url"]
                    if extra.get("location"):
                        payload["location"] = extra["location"]
                    if extra.get("website_url"):
                        payload["website_url"] = extra["website_url"]

        payloads = list(seen.values())
        _set_result(status="running", message=f"Saving {len(payloads)} calls…")
        for start in range(0, len(payloads), UPSERT_CHUNK):
            chunk = payloads[start : start + UPSERT_CHUNK]
            with session_scope() as session:
                for payload in chunk:
                    upsert_cfp_call(session, payload)
                    upserted += 1
                    if payload.get("deadline"):
                        with_deadline += 1
        message = f"Loaded {upserted} calls ({with_deadline} with submission deadlines)."
        if upserted == 0:
            message = "WikiCFP returned no results. Check network access to www.wikicfp.com."
        logger.info(
            "CFP refresh finished: %s upserted, %s enriched, %s with deadlines",
            upserted,
            enriched,
            with_deadline,
        )
        _set_result(status="ok", message=message)
        return {"skipped": 0, "upserted": upserted, "enriched": enriched, "with_deadline": with_deadline}
    except Exception:
        logger.exception("CFP refresh failed")
        _set_result(status="error", message="Call for Papers refresh failed. See server logs.")
        return {"skipped": 0, "upserted": upserted, "enriched": enriched, "error": 1}
    finally:
        if claim:
            with _refresh_lock:
                _refreshing = False
                _refresh_started_at = 0.0


def refresh_cfps_sync(*, force: bool = False, claim: bool = True) -> dict[str, int]:
    return refresh_cfps(force=force, claim=claim)


def schedule_cfp_refresh(*, force: bool = False) -> bool:
    """Fire-and-forget background refresh. Returns False if a refresh is already running."""
    global _refreshing, _refresh_started_at
    _clear_stale_refresh()
    with _refresh_lock:
        if _refreshing:
            return False
        _refreshing = True
        _refresh_started_at = time.monotonic()
        _set_result(status="running", message="Refreshing from WikiCFP…")

    def _run() -> None:
        global _refreshing, _refresh_started_at
        try:
            if not force and not should_refresh(force=False):
                _set_result(status="skipped", message="Cache is still fresh (refreshed within 6 hours).")
                return
            refresh_cfps(force=True, claim=False)
        except Exception:
            logger.exception("Background CFP refresh failed")
            _set_result(status="error", message="Call for Papers refresh failed. See server logs.")
        finally:
            with _refresh_lock:
                _refreshing = False
                _refresh_started_at = 0.0

    threading.Thread(target=_run, name="cfp-refresh", daemon=True).start()
    return True

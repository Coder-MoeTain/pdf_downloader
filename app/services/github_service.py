"""Fetch and cache the top GitHub repositories for research categories."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from app.config import get_runtime_config
from app.database.connection import session_scope
from app.database.repository import replace_github_category_repos
from app.utils.logger import get_logger
from app.utils.time import utc_now

logger = get_logger("app.github")

GITHUB_SEARCH = "https://api.github.com/search/repositories"
REPOS_PER_CATEGORY = 10
SEARCH_CANDIDATES = 40
REQUEST_TIMEOUT = 20.0
STALE_REFRESH_SECONDS = 240.0
_FRAMEWORK_LIBRARY_TOPICS = {
    "framework",
    "library",
    "libraries",
    "python-library",
    "javascript-library",
    "ruby-library",
    "ml-framework",
    "deep-learning-framework",
    "web-framework",
    "web-frameworks",
}
_FRAMEWORK_LIBRARY_NAMES = {
    "tensorflow",
    "pytorch",
    "keras",
    "numpy",
    "pandas",
    "scikit-learn",
    "sklearn",
    "transformers",
    "jax",
    "mxnet",
    "caffe",
    "opencv",
    "scipy",
    "matplotlib",
    "seaborn",
    "spacy",
    "nltk",
    "xgboost",
    "lightgbm",
    "catboost",
    "pytorch-lightning",
    "lightning",
    "diffusers",
    "tokenizers",
    "huggingface_hub",
    "bert",
    "streamlit",
    "hanlp",
    "biopython",
}
_FRAMEWORK_LIBRARY_RE = re.compile(
    r"\b(?:an?\s+)?(?:open[\s-]?source\s+)?(?:machine[\s-]?learning\s+|deep[\s-]?learning\s+|web\s+|python\s+)?"
    r"(?:framework|library|libraries)\b",
    re.I,
)

GITHUB_CATEGORIES: tuple[dict[str, str], ...] = (
    {"slug": "machine-learning", "label": "Machine learning", "topic": "machine-learning"},
    {"slug": "artificial-intelligence", "label": "Artificial intelligence", "topic": "artificial-intelligence"},
    {"slug": "cybersecurity", "label": "Cybersecurity", "topic": "cybersecurity"},
    {"slug": "computer-vision", "label": "Computer vision", "topic": "computer-vision"},
    {"slug": "nlp", "label": "NLP", "topic": "nlp"},
    {"slug": "data-science", "label": "Data science", "topic": "data-science"},
    {"slug": "robotics", "label": "Robotics", "topic": "robotics"},
    {"slug": "bioinformatics", "label": "Bioinformatics", "topic": "bioinformatics"},
    {"slug": "cryptography", "label": "Cryptography", "topic": "cryptography"},
    {"slug": "quantum-computing", "label": "Quantum computing", "topic": "quantum-computing"},
)

_refresh_lock = threading.RLock()
_refreshing = False
_refresh_started_at = 0.0
_last_refresh_result: dict[str, Any] = {"status": "idle", "message": ""}


def github_category_map() -> dict[str, dict[str, str]]:
    return {item["slug"]: item for item in GITHUB_CATEGORIES}


def category_label(slug: str) -> str:
    item = github_category_map().get(slug)
    return item["label"] if item else slug.replace("-", " ").title()


def project_name(repo: Any) -> str:
    """Public project name: repository name, not owner/login."""
    if isinstance(repo, dict):
        name = str(repo.get("name") or "").strip()
        full_name = str(repo.get("full_name") or "").strip()
    else:
        name = str(getattr(repo, "name", "") or "").strip()
        full_name = str(getattr(repo, "full_name", "") or "").strip()
    return name or (full_name.split("/")[-1] if full_name else "")


def topic_list(value: object | None) -> list[str]:
    if isinstance(value, list):
        parts = value
    else:
        text = str(value or "").replace(",", " ")
        parts = text.split()
    seen: set[str] = set()
    items: list[str] = []
    for part in parts:
        topic = str(part or "").strip()
        key = topic.lower()
        if not topic or key in seen:
            continue
        seen.add(key)
        items.append(topic)
    return items


def is_framework_or_library(repo: Any) -> bool:
    """True for ML/web frameworks and libraries; those are omitted from Top projects."""
    if isinstance(repo, dict):
        name = str(repo.get("name") or "")
        full_name = str(repo.get("full_name") or "")
        description = str(repo.get("description") or "")
        topics = repo.get("topics")
    else:
        name = str(getattr(repo, "name", "") or "")
        full_name = str(getattr(repo, "full_name", "") or "")
        description = str(getattr(repo, "description", "") or "")
        topics = getattr(repo, "topics", None)
    slug = (name or full_name.split("/")[-1]).strip().lower()
    if slug in _FRAMEWORK_LIBRARY_NAMES:
        return True
    topics_set = {item.lower() for item in topic_list(topics)}
    if topics_set & _FRAMEWORK_LIBRARY_TOPICS:
        return True
    if any(item.endswith("-library") or item.endswith("-framework") or item.endswith("-libraries") for item in topics_set):
        return True
    return bool(_FRAMEWORK_LIBRARY_RE.search(description))


def select_project_repos(rows: list[Any], *, limit: int | None = REPOS_PER_CATEGORY) -> list[Any]:
    """Keep research projects only, in the original star order."""
    selected = [row for row in rows if not is_framework_or_library(row)]
    if limit is None:
        return selected
    return selected[: max(0, int(limit))]


def _repo_field(repo: Any, name: str, default: Any = "") -> Any:
    if isinstance(repo, dict):
        return repo.get(name, default)
    return getattr(repo, name, default)


def merge_project_repos(rows: list[Any]) -> list[tuple[Any, tuple[str, ...]]]:
    """Dedupe by repository, keep the highest star count, and collect categories."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        full_name = str(_repo_field(row, "full_name") or "").strip()
        key = full_name.lower()
        if not key:
            continue
        stars = int(_repo_field(row, "stars", 0) or 0)
        category = str(_repo_field(row, "category") or "").strip()
        item = grouped.get(key)
        if item is None:
            grouped[key] = {
                "row": row,
                "stars": stars,
                "categories": [category] if category else [],
            }
            continue
        if category and category not in item["categories"]:
            item["categories"].append(category)
        if stars > item["stars"]:
            item["row"] = row
            item["stars"] = stars
    merged = list(grouped.values())
    merged.sort(key=lambda item: (-int(item["stars"]), project_name(item["row"]).lower()))
    return [(item["row"], tuple(item["categories"])) for item in merged]


def repo_detail_payload(repo: Any, *, rank: int) -> dict[str, Any]:
    if isinstance(repo, dict):
        full_name = str(repo.get("full_name") or "")
        owner = str(repo.get("owner_login") or "").strip() or (full_name.split("/")[0] if "/" in full_name else "")
        description = str(repo.get("description") or "").strip()
        homepage = _http_url(repo.get("homepage"))
        language = str(repo.get("language") or "").strip()
        html_url = str(repo.get("html_url") or "")
        avatar = _http_url(repo.get("owner_avatar_url"))
        stars = int(repo.get("stars") or 0)
        forks = int(repo.get("forks") or 0)
        topics = topic_list(repo.get("topics"))
        category = str(repo.get("category") or "")
    else:
        full_name = str(getattr(repo, "full_name", "") or "")
        owner = str(getattr(repo, "owner_login", "") or "").strip() or (
            full_name.split("/")[0] if "/" in full_name else ""
        )
        description = str(getattr(repo, "description", "") or "").strip()
        homepage = _http_url(getattr(repo, "homepage", None))
        language = str(getattr(repo, "language", "") or "").strip()
        html_url = str(getattr(repo, "html_url", "") or "")
        avatar = _http_url(getattr(repo, "owner_avatar_url", None))
        stars = int(getattr(repo, "stars", 0) or 0)
        forks = int(getattr(repo, "forks", 0) or 0)
        topics = topic_list(getattr(repo, "topics", None))
        category = str(getattr(repo, "category", "") or "")
    name = project_name(repo)
    return {
        "key": f"{category}:{full_name}",
        "rank": rank,
        "name": name,
        "owner": owner,
        "full_name": full_name,
        "description": description,
        "homepage": homepage or "",
        "language": language,
        "html_url": html_url,
        "avatar": avatar or "",
        "stars": stars,
        "stars_label": format_star_count(stars),
        "forks_label": format_star_count(forks),
        "topics": topics,
        "category": category,
        "category_label": category_label(category) if category else "",
    }


def format_star_count(value: int) -> str:
    count = max(0, int(value or 0))
    if count >= 1_000_000:
        text = f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        text = f"{count / 1_000:.1f}k"
    else:
        return str(count)
    return text.replace(".0k", "k").replace(".0M", "M")


def _http_url(value: object | None) -> str | None:
    text = str(value or "").strip()
    if text.startswith("https://") or text.startswith("http://"):
        return text
    return None


def parse_search_items(payload: dict[str, Any], *, fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    """Turn a GitHub search JSON body into stored repo dicts."""
    now = fetched_at or utc_now()
    rows: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        full_name = str(item.get("full_name") or "").strip()
        html_url = str(item.get("html_url") or "").strip()
        if not full_name or not html_url.startswith("https://github.com/"):
            continue
        owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        topics = item.get("topics") if isinstance(item.get("topics"), list) else []
        parsed = {
            "full_name": full_name[:255],
            "name": str(item.get("name") or full_name.split("/")[-1])[:255],
            "description": (str(item.get("description") or "").strip() or None),
            "html_url": html_url,
            "homepage": _http_url(item.get("homepage")),
            "language": (str(item.get("language") or "").strip() or None),
            "stars": int(item.get("stargazers_count") or 0),
            "forks": int(item.get("forks_count") or 0),
            "topics": ", ".join(str(topic) for topic in topics if topic)[:1000] or None,
            "owner_login": (str(owner.get("login") or "").strip() or None),
            "owner_avatar_url": _http_url(owner.get("avatar_url")),
            "fetched_at": now,
        }
        if is_framework_or_library(parsed):
            continue
        rows.append(parsed)
        if len(rows) >= REPOS_PER_CATEGORY:
            break
    return rows


def refresh_status() -> dict[str, Any]:
    _clear_stale_refresh()
    with _refresh_lock:
        return {"running": _refreshing, **dict(_last_refresh_result)}


def _set_result(**kwargs: Any) -> None:
    global _last_refresh_result
    with _refresh_lock:
        _last_refresh_result = {**_last_refresh_result, **kwargs}


def _clear_stale_refresh() -> None:
    global _refreshing, _refresh_started_at
    with _refresh_lock:
        if not _refreshing:
            return
        if _refresh_started_at and (time.monotonic() - _refresh_started_at) > STALE_REFRESH_SECONDS:
            _refreshing = False
            _refresh_started_at = 0.0
            _last_refresh_result = {
                "status": "error",
                "message": "Previous GitHub refresh timed out. Try Refresh again.",
            }
            logger.warning("Cleared stale GitHub refresh lock after %.0fs", STALE_REFRESH_SECONDS)


def _request_headers() -> dict[str, str]:
    cfg = get_runtime_config()
    headers = {
        "User-Agent": cfg.user_agent_header(),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = str(getattr(cfg.env, "github_token", "") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _has_github_token() -> bool:
    cfg = get_runtime_config()
    return bool(str(getattr(cfg.env, "github_token", "") or "").strip())


def _http_get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=_request_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("GitHub search did not return an object")
    return data


def _search_topic(topic: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "q": f"topic:{topic} -topic:framework -topic:library",
            "sort": "stars",
            "order": "desc",
            "per_page": str(SEARCH_CANDIDATES),
        }
    )
    url = f"{GITHUB_SEARCH}?{query}"
    try:
        payload = _http_get_json(url)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("GitHub search failed for topic %s: %s", topic, exc)
        return []
    return parse_search_items(payload)


def refresh_github_repos(*, claim: bool = True) -> dict[str, int]:
    """Fetch the top 10 GitHub repos for each curated category (sync)."""
    global _refreshing, _refresh_started_at
    if claim:
        with _refresh_lock:
            if _refreshing:
                _set_result(status="busy", message="A GitHub refresh is already running.")
                return {"skipped": 1, "categories": 0, "repos": 0}
            _refreshing = True
            _refresh_started_at = time.monotonic()
            _set_result(status="running", message="Refreshing top GitHub projects…")
    saved = 0
    categories = 0
    pause = 1.2 if _has_github_token() else 6.5
    try:
        for index, item in enumerate(GITHUB_CATEGORIES):
            _set_result(status="running", message=f"Fetching {item['label']} repositories…")
            repos = _search_topic(item["topic"])
            if repos:
                with session_scope() as session:
                    saved += replace_github_category_repos(session, item["slug"], repos)
                categories += 1
            if index < len(GITHUB_CATEGORIES) - 1:
                time.sleep(pause)
        message = f"Loaded {saved} repositories across {categories} categories."
        if saved == 0:
            message = "GitHub returned no repositories. Check network access or set GITHUB_TOKEN."
            _set_result(status="error", message=message)
        else:
            _set_result(status="ok", message=message)
        logger.info("GitHub refresh finished: %s repos in %s categories", saved, categories)
        return {"skipped": 0, "categories": categories, "repos": saved}
    except Exception:
        logger.exception("GitHub refresh failed")
        _set_result(status="error", message="GitHub refresh failed. See server logs.")
        return {"skipped": 0, "categories": categories, "repos": saved, "error": 1}
    finally:
        if claim:
            with _refresh_lock:
                _refreshing = False
                _refresh_started_at = 0.0


def schedule_github_refresh() -> bool:
    """Fire-and-forget background refresh. Returns False if a refresh is already running."""
    global _refreshing, _refresh_started_at
    _clear_stale_refresh()
    with _refresh_lock:
        if _refreshing:
            return False
        _refreshing = True
        _refresh_started_at = time.monotonic()
        _set_result(status="running", message="Refreshing top GitHub projects…")

    def _run() -> None:
        global _refreshing, _refresh_started_at
        try:
            refresh_github_repos(claim=False)
        except Exception:
            logger.exception("Background GitHub refresh failed")
            _set_result(status="error", message="GitHub refresh failed. See server logs.")
        finally:
            with _refresh_lock:
                _refreshing = False
                _refresh_started_at = 0.0

    threading.Thread(target=_run, name="github-refresh", daemon=True).start()
    return True

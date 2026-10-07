"""URL safety checks, robots.txt lookup, and PDF byte validation."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from app.config import get_runtime_config
from app.security.ssrf import is_safe_url as _is_safe_url
from app.utils.logger import get_logger

logger = get_logger("app.security")

PDF_MAGIC = b"%PDF-"
ALLOWED_SCHEMES = {"https", "http"}
CONTENT_TYPE_PDF = re.compile(r"application/(pdf|octet-stream)", re.I)
ROBOTS_FETCH_TIMEOUT_SECONDS = 8.0

# None = fetch failed / timed out → treat as allow (and do not retry every paper).
_robots_cache: dict[str, RobotFileParser | None] = {}


def is_safe_url(url: str | None, *, prefer_https: bool = True, resolve_dns: bool = False) -> bool:
    """Hostname/scheme check. DNS resolution is performed by the HTTP client before fetch."""
    return _is_safe_url(url, prefer_https=prefer_https, resolve_dns=resolve_dns)


def looks_like_pdf(content_type: str | None, first_bytes: bytes, min_size: int, total_size: int) -> bool:
    if total_size < min_size:
        return False
    if not first_bytes.startswith(PDF_MAGIC):
        return False
    if content_type:
        mime = content_type.split(";")[0].strip()
        if (
            mime
            and not CONTENT_TYPE_PDF.search(mime)
            and mime not in {"binary/octet-stream", "application/octet-stream"}
        ):
            # Some repositories send application/force-download; magic bytes already checked.
            if "html" in mime.lower() or "json" in mime.lower() or "text/" in mime.lower():
                return False
    return True


def robots_allowed(url: str, user_agent: str) -> bool:
    cfg = get_runtime_config()
    if not cfg.check_robots_txt:
        return True
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return True
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    if robots_url in _robots_cache:
        parser = _robots_cache[robots_url]
        if parser is None:
            return True
        try:
            return parser.can_fetch(user_agent, url)
        except Exception:
            return True

    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        # RobotFileParser.read() uses urllib with no timeout and can hang forever
        # on broken HTTP/2 hosts (e.g. aic.gov.au), blocking download slots.
        request = urllib.request.Request(
            robots_url,
            headers={"User-Agent": user_agent or "CyberScholar"},
        )
        with urllib.request.urlopen(request, timeout=ROBOTS_FETCH_TIMEOUT_SECONDS) as response:
            raw = response.read(512_000)
        parser.parse(raw.decode("utf-8", errors="replace").splitlines())
        _robots_cache[robots_url] = parser
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        logger.info("Could not read robots.txt at %s (%s); allowing download", robots_url, exc)
        _robots_cache[robots_url] = None
        return True
    except Exception as exc:
        logger.info("Could not read robots.txt at %s (%s); allowing download", robots_url, exc)
        _robots_cache[robots_url] = None
        return True
    try:
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()

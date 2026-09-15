"""URL safety checks, robots.txt lookup, and PDF byte validation."""

from __future__ import annotations

import re
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from app.config import get_runtime_config
from app.security.ssrf import is_safe_url as _is_safe_url
from app.utils.logger import get_logger

logger = get_logger("app.security")

PDF_MAGIC = b"%PDF-"
ALLOWED_SCHEMES = {"https", "http"}
CONTENT_TYPE_PDF = re.compile(r"application/(pdf|octet-stream)", re.I)

_robots_cache: dict[str, RobotFileParser] = {}


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
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = _robots_cache.get(robots_url)
    if parser is None:
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            parser.read()
        except Exception:
            logger.info("Could not read robots.txt at %s; allowing download", robots_url)
            _robots_cache[robots_url] = parser
            return True
        _robots_cache[robots_url] = parser
    try:
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()

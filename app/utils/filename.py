"""Safe filename and path helpers for cross-platform PDF storage."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")
NON_SLUG = re.compile(r"[^a-z0-9]+")


def sanitize_component(value: str, max_length: int = 80, fallback: str = "untitled") -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = UNSAFE_CHARS.sub("_", text)
    text = WHITESPACE.sub("_", text).strip(" ._")
    text = re.sub(r"_+", "_", text)
    if not text:
        text = fallback
    if text.upper() in WINDOWS_RESERVED:
        text = f"_{text}"
    return text[:max_length].rstrip(" .")


def slugify(value: str, max_length: int = 60) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = NON_SLUG.sub("_", text).strip("_")
    return (text or "topic")[:max_length]


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    text = unicodedata.normalize("NFKD", title)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = WHITESPACE.sub(" ", text).strip()
    return text


def paper_filename(
    year: int | None,
    first_author: str,
    title: str,
    doi: str | None,
    max_length: int = 120,
) -> str:
    year_part = str(year) if year else "0000"
    author_part = sanitize_component(first_author.split(",")[0].split()[-1], max_length=24, fallback="Unknown")
    words = [w for w in re.sub(r"[^A-Za-z0-9\s]", " ", title or "").split() if w]
    title_part = sanitize_component("_".join(words[:8]), max_length=60, fallback="paper")
    doi_part = sanitize_component((doi or "nodoi").replace("/", "_"), max_length=40, fallback="nodoi")
    name = f"{year_part}_{author_part}_{title_part}_{doi_part}.pdf"
    if len(name) > max_length + 4:
        keep = max_length - len(year_part) - len(author_part) - len(doi_part) - 8
        title_part = title_part[: max(8, keep)]
        name = f"{year_part}_{author_part}_{title_part}_{doi_part}.pdf"
    return name


def safe_join(base: Path, *parts: str) -> Path:
    """Join path parts and reject traversal outside *base*."""
    resolved_base = base.resolve()
    candidate = resolved_base.joinpath(*parts).resolve()
    if not str(candidate).startswith(str(resolved_base)):
        raise ValueError(f"Path traversal detected: {candidate}")
    return candidate

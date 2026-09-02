"""Shared UI helpers for status labels and navigation."""

from __future__ import annotations

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


def active_page(path: str) -> str:
    mapping = (
        ("/search", "search"),
        ("/library", "library"),
        ("/downloads", "downloads"),
        ("/sources", "sources"),
        ("/statistics", "statistics"),
        ("/settings", "settings"),
        ("/papers", "library"),
    )
    for prefix, name in mapping:
        if path.startswith(prefix):
            return name
    return "dashboard"

"""Configurable synonym-based query expansion."""

from __future__ import annotations

import re

from app.config import QueryExpansionConfig, load_config


def expand_query(query: str, config: QueryExpansionConfig | None = None) -> list[str]:
    """Return the original query plus a small set of synonym expansions."""
    cfg = config or load_config().query_expansion
    original = " ".join(query.split()).strip()
    if not original or not cfg.enabled:
        return [original] if original else []

    expansions: list[str] = []
    lower = original.lower()
    items = sorted(cfg.synonyms.items(), key=lambda kv: len(kv[0]), reverse=True)

    for key, synonyms in items:
        pattern = re.compile(rf"\b{re.escape(key)}\b", re.IGNORECASE)
        if not pattern.search(original):
            continue
        for synonym in synonyms:
            candidate = pattern.sub(synonym, original, count=1)
            candidate = " ".join(candidate.split())
            if candidate.lower() != lower and candidate not in expansions:
                expansions.append(candidate)
            if len(expansions) >= cfg.max_expanded:
                break
        if len(expansions) >= cfg.max_expanded:
            break

    return [original, *expansions[: cfg.max_expanded]]

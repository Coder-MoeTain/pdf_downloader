"""Keyword (default) and optional semantic relevance ranking."""

from __future__ import annotations

import math
import re
from datetime import datetime
from functools import lru_cache

from rapidfuzz import fuzz

from app.config import RankingConfig, load_config
from app.models.paper import PaperRecord
from app.models.search import SearchFilters, SortMode
from app.utils.filename import normalize_title

TOKEN_RE = re.compile(r"[a-z0-9]+")

_semantic_model = None
_semantic_failed = False


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(normalize_title(text))


def text_relevance(query: str, text: str | None) -> float:
    if not text:
        return 0.0
    q_tokens = set(tokenize(query))
    t_tokens = set(tokenize(text))
    if not q_tokens:
        return 0.0
    overlap = len(q_tokens & t_tokens) / len(q_tokens)
    fuzzy = fuzz.token_set_ratio(query, text) / 100.0
    return max(0.0, min(1.0, 0.55 * overlap + 0.45 * fuzzy))


def citation_score(count: int | None, cap: int) -> float:
    if not count or count <= 0:
        return 0.0
    return min(1.0, math.log1p(count) / math.log1p(cap))


def recency_score(year: int | None, horizon: int, current_year: int | None = None) -> float:
    if not year:
        return 0.3
    now = current_year or datetime.now().year
    age = max(0, now - year)
    return max(0.0, 1.0 - (age / max(horizon, 1)))


def _semantic_similarity(query: str, title: str, abstract: str | None) -> float | None:
    global _semantic_model, _semantic_failed
    if _semantic_failed:
        return None
    try:
        from sentence_transformers import SentenceTransformer, util
    except ImportError:
        _semantic_failed = True
        return None
    if _semantic_model is None:
        cfg = load_config().ranking
        _semantic_model = SentenceTransformer(cfg.semantic_model)
    text = f"{title}. {abstract or ''}"
    embeddings = _semantic_model.encode([query, text], convert_to_tensor=True)
    score = float(util.cos_sim(embeddings[0], embeddings[1]).item())
    return max(0.0, min(1.0, (score + 1) / 2 if score < 0 else score))


@lru_cache(maxsize=1)
def _weights(config_key: str = "default") -> RankingConfig:
    return load_config().ranking


def score_paper(paper: PaperRecord, query: str, expanded: list[str] | None = None, config: RankingConfig | None = None) -> float:
    cfg = config or _weights()
    queries = [query, *(expanded or [])]
    title_scores = [text_relevance(q, paper.title) for q in queries]
    abstract_scores = [text_relevance(q, paper.abstract) for q in queries]
    keyword_text = " ".join((paper.keywords or []) + (paper.research_fields or []))
    keyword_scores = [text_relevance(q, keyword_text) for q in queries]
    title = max(title_scores) if title_scores else 0.0
    abstract = max(abstract_scores) if abstract_scores else 0.0
    keywords = max(keyword_scores) if keyword_scores else 0.0
    citations = citation_score(paper.citation_count, cfg.citation_cap)
    recency = recency_score(paper.publication_year, cfg.recency_horizon_years)

    if cfg.semantic_enabled:
        semantic = _semantic_similarity(query, paper.title, paper.abstract)
        if semantic is not None:
            title = 0.6 * title + 0.4 * semantic
            abstract = 0.6 * abstract + 0.4 * semantic

    total = (
        cfg.title_weight * title
        + cfg.abstract_weight * abstract
        + cfg.keyword_weight * keywords
        + cfg.citation_weight * citations
        + cfg.recency_weight * recency
    )
    return round(max(0.0, min(100.0, total * 100.0)), 2)


def rank_papers(
    papers: list[PaperRecord],
    filters: SearchFilters,
    expanded: list[str] | None = None,
    config: RankingConfig | None = None,
) -> list[PaperRecord]:
    for paper in papers:
        paper.relevance_score = score_paper(paper, filters.query, expanded, config)

    mode = filters.sort
    if mode == SortMode.CITATIONS:
        papers.sort(key=lambda p: (p.citation_count or 0, p.relevance_score), reverse=True)
    elif mode == SortMode.NEWEST:
        papers.sort(key=lambda p: (p.publication_year or 0, p.relevance_score), reverse=True)
    else:
        papers.sort(key=lambda p: p.relevance_score, reverse=True)
    return papers

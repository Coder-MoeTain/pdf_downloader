"""Duplicate detection using identifiers and fuzzy title matching."""

from __future__ import annotations

from collections import defaultdict

from rapidfuzz import fuzz

from app.config import DedupConfig, load_config
from app.models.paper import PaperRecord
from app.services.merge_service import merge_papers
from app.utils.doi import normalize_doi
from app.utils.filename import normalize_title


def first_author_key(paper: PaperRecord) -> str:
    if not paper.authors:
        return ""
    return normalize_title(paper.authors[0].name.split()[-1])


def identity_keys(paper: PaperRecord) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    doi = normalize_doi(paper.doi)
    if doi:
        keys.append(("doi", doi))
    if paper.pmid:
        keys.append(("pmid", str(paper.pmid).strip()))
    if paper.arxiv_id:
        keys.append(("arxiv", paper.arxiv_id.strip().lower()))
    if paper.openalex_id:
        keys.append(("openalex", paper.openalex_id.strip().lower()))
    if paper.semantic_scholar_id:
        keys.append(("s2", paper.semantic_scholar_id.strip().lower()))
    return keys


def title_year_author_key(paper: PaperRecord) -> str | None:
    title = normalize_title(paper.title)
    if not title:
        return None
    return f"{title}|{paper.publication_year or ''}|{first_author_key(paper)}"


def titles_are_duplicates(a: str, b: str, threshold: int) -> bool:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return fuzz.token_set_ratio(na, nb) >= threshold


def deduplicate(papers: list[PaperRecord], config: DedupConfig | None = None) -> tuple[list[PaperRecord], int]:
    """Merge duplicate records. Returns (unique_papers, duplicates_removed)."""
    cfg = config or load_config().dedup
    if not papers:
        return [], 0

    parent: list[int] = list(range(len(papers)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    index: dict[tuple[str, str], int] = {}
    title_index: dict[str, int] = {}

    for i, paper in enumerate(papers):
        for key in identity_keys(paper):
            if key in index:
                union(i, index[key])
            else:
                index[key] = i
        combo = title_year_author_key(paper)
        if combo:
            if combo in title_index:
                union(i, title_index[combo])
            else:
                title_index[combo] = i

    if cfg.use_fuzzy_title:
        by_year: dict[int | None, list[int]] = defaultdict(list)
        for i, paper in enumerate(papers):
            by_year[paper.publication_year].append(i)
        for indices in by_year.values():
            for a_pos, i in enumerate(indices):
                for j in indices[a_pos + 1 :]:
                    if find(i) == find(j):
                        continue
                    left, right = papers[i], papers[j]
                    same_author = (not first_author_key(left)) or first_author_key(left) == first_author_key(right)
                    if same_author and titles_are_duplicates(left.title, right.title, cfg.title_similarity_threshold):
                        union(i, j)

    groups: dict[int, list[PaperRecord]] = defaultdict(list)
    for i, paper in enumerate(papers):
        groups[find(i)].append(paper)

    unique = [merge_papers(group) if len(group) > 1 else group[0] for group in groups.values()]
    removed = len(papers) - len(unique)
    return unique, removed

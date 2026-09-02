from app.models.paper import PaperRecord
from app.models.search import SearchFilters, SortMode
from app.services.ranking_service import rank_papers, score_paper, text_relevance


def test_title_relevance_higher_for_close_match():
    query = "web application security"
    strong = text_relevance(query, "Machine learning for web application security")
    weak = text_relevance(query, "Satellite communications handbook")
    assert strong > weak


def test_score_range_and_sort():
    query = "reinforcement learning penetration testing"
    papers = [
        PaperRecord(title="Reinforcement learning for autonomous penetration testing", abstract="RL pentest", publication_year=2025, citation_count=10),
        PaperRecord(title="Unrelated astronomy paper", abstract="stars", publication_year=1999, citation_count=5000),
    ]
    filters = SearchFilters(query=query, sort=SortMode.RELEVANCE)
    ranked = rank_papers(papers, filters)
    assert ranked[0].title.startswith("Reinforcement")
    for paper in ranked:
        assert 0 <= paper.relevance_score <= 100


def test_sort_newest():
    papers = [
        PaperRecord(title="Old RL pentest", publication_year=2018),
        PaperRecord(title="New RL pentest", publication_year=2026),
    ]
    ranked = rank_papers(papers, SearchFilters(query="RL pentest", sort=SortMode.NEWEST))
    assert ranked[0].publication_year == 2026

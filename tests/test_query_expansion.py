from app.services.query_expansion import expand_query
from app.config import QueryExpansionConfig


def test_expansion_includes_original_and_synonyms():
    cfg = QueryExpansionConfig(
        enabled=True,
        max_expanded=5,
        synonyms={"AI": ["artificial intelligence"], "IDS": ["intrusion detection system"]},
    )
    result = expand_query("AI web IDS", cfg)
    assert result[0] == "AI web IDS"
    assert any("artificial intelligence" in q.lower() for q in result)
    assert len(result) <= 6


def test_expansion_disabled():
    cfg = QueryExpansionConfig(enabled=False, synonyms={"AI": ["artificial intelligence"]})
    assert expand_query("AI security", cfg) == ["AI security"]

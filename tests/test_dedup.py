from app.models.paper import AuthorRecord, PaperRecord
from app.services.dedup_service import deduplicate, titles_are_duplicates
from app.utils.filename import normalize_title


def _paper(title, **kwargs):
    authors = kwargs.pop("authors", [AuthorRecord(name="Jane Smith")])
    return PaperRecord(title=title, authors=authors, **kwargs)


def test_doi_dedup():
    papers = [
        _paper("Paper A", doi="https://doi.org/10.1000/abc", source_provider="crossref"),
        _paper("Paper A copy", doi="10.1000/ABC", source_provider="openalex"),
    ]
    unique, removed = deduplicate(papers)
    assert len(unique) == 1
    assert removed == 1
    assert unique[0].doi == "10.1000/abc"


def test_fuzzy_title_dedup():
    papers = [
        _paper(
            "Machine Learning Based Detection of SQL Injection Attacks",
            publication_year=2024,
        ),
        _paper(
            "Machine-learning-based detection of SQL injection attack",
            publication_year=2024,
        ),
    ]
    assert titles_are_duplicates(papers[0].title, papers[1].title, 92)
    unique, removed = deduplicate(papers)
    assert len(unique) == 1
    assert removed == 1


def test_different_papers_kept():
    papers = [
        _paper("Reinforcement Learning for Robotics", publication_year=2023, doi="10.1000/a"),
        _paper("Web Application Firewall Survey", publication_year=2023, doi="10.1000/b"),
    ]
    unique, removed = deduplicate(papers)
    assert len(unique) == 2
    assert removed == 0


def test_title_normalization_helper():
    assert normalize_title("SQL-Injection!") == "sql injection"

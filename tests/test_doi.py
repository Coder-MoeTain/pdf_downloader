from app.utils.doi import doi_url, extract_doi, normalize_doi


def test_normalize_variants():
    expected = "10.1234/example.abc"
    assert normalize_doi("https://doi.org/10.1234/example.abc") == expected
    assert normalize_doi("http://dx.doi.org/10.1234/example.abc") == expected
    assert normalize_doi("doi:10.1234/example.abc") == expected
    assert normalize_doi("DOI:10.1234/example.ABC") == expected
    assert normalize_doi("10.1234/example.abc") == expected


def test_normalize_rejects_garbage():
    assert normalize_doi(None) is None
    assert normalize_doi("") is None
    assert normalize_doi("not-a-doi") is None


def test_extract_and_url():
    text = "See https://doi.org/10.1038/s41586-020-2649-2 for details."
    doi = extract_doi(text)
    assert doi == "10.1038/s41586-020-2649-2"
    assert doi_url(doi) == "https://doi.org/10.1038/s41586-020-2649-2"

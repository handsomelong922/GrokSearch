from grok_search.sources import split_answer_and_sources


def test_inline_citation_urls_are_exposed_without_stripping_answer():
    text = (
        "Answer with inline evidence [[1]](https://example.test/one) and "
        "[[2]](https://example.test/two)."
    )

    answer, sources = split_answer_and_sources(text)

    assert answer == text
    assert [item["url"] for item in sources] == [
        "https://example.test/one",
        "https://example.test/two",
    ]


def test_plain_inline_url_is_exposed_as_source():
    text = "See https://example.test/reference for details."

    answer, sources = split_answer_and_sources(text)

    assert answer == text
    assert sources == [{"url": "https://example.test/reference"}]

from grok_search import server


def test_extra_source_budget_is_split_when_both_providers_are_available():
    assert server._allocate_extra_sources(4, has_tavily=True, has_firecrawl=True) == (2, 2)
    assert server._allocate_extra_sources(3, has_tavily=True, has_firecrawl=True) == (1, 2)


def test_extra_source_budget_goes_to_only_available_provider():
    assert server._allocate_extra_sources(3, has_tavily=True, has_firecrawl=False) == (3, 0)
    assert server._allocate_extra_sources(3, has_tavily=False, has_firecrawl=True) == (0, 3)
    assert server._allocate_extra_sources(3, has_tavily=False, has_firecrawl=False) == (0, 0)

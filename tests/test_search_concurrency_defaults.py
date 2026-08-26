from grok_search import server


def test_search_concurrency_is_unlimited_by_default(monkeypatch):
    """Distinct web searches must not be serialized by a hidden default cap."""
    monkeypatch.delenv("MAX_CONCURRENT_SEARCHES", raising=False)

    assert not hasattr(server, "_SEARCH_CONCURRENCY_SEMAPHORE")

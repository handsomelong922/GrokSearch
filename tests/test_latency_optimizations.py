import asyncio

import pytest

from grok_search import server


@pytest.fixture
def isolated_search_state(monkeypatch):
    monkeypatch.setattr(server, "_RESULT_CACHE", server.SearchResultCache())
    monkeypatch.setattr(server, "_WEB_SEARCH_SINGLE_FLIGHT",
                        server._SearchSingleFlight())


@pytest.mark.asyncio
async def test_supplemental_requests_reuse_client_and_keep_request_timeouts(
        monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "firecrawl-key")
    monkeypatch.setenv("TAVILY_API_URL", "https://tavily.test")
    monkeypatch.setenv("FIRECRAWL_API_URL", "https://firecrawl.test")
    server.config._tavily_key_index = 0
    created = []
    requests = []

    class FakeResponse:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        def raise_for_status(self):
            return None

        def json(self):
            if self.endpoint.endswith("/extract"):
                return {"results": [{"raw_content": "document"}]}
            if self.endpoint.endswith("/search") and "firecrawl" in self.endpoint:
                return {"data": {"web": [{"url": "https://fire.test"}]}}
            if self.endpoint.endswith("/search"):
                return {"results": [{"url": "https://tavily.test"}]}
            if self.endpoint.endswith("/scrape"):
                return {"data": {"markdown": "page"}}
            return {"base_url": "https://example.test", "results": []}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.is_closed = False
            created.append(kwargs)

        async def post(self, endpoint, **kwargs):
            requests.append((endpoint, kwargs["timeout"]))
            return FakeResponse(endpoint)

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

    await server._reset_supplemental_client()
    try:
        assert await server._call_tavily_search("query", 1)
        assert await server._call_tavily_extract("https://example.test") == "document"
        assert await server._call_firecrawl_search("query", 1)
        assert await server._call_firecrawl_scrape("https://example.test") == "page"
        assert "base_url" in await server._call_tavily_map(
            "https://example.test", timeout=25)
    finally:
        await server._reset_supplemental_client()

    assert len(created) == 1
    assert "follow_redirects" not in created[0]
    limits = created[0]["limits"]
    assert limits.max_connections == 20
    assert limits.max_keepalive_connections == 10
    assert [timeout.read for _, timeout in requests] == [90.0, 60.0, 90.0,
                                                          90.0, 35.0]
    assert all(timeout.pool == 5.0 for _, timeout in requests)


@pytest.mark.asyncio
async def test_supplemental_client_is_initialized_once_under_concurrency(
        monkeypatch):
    created = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.is_closed = False
            created.append(kwargs)

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

    await server._reset_supplemental_client()
    try:
        clients = await asyncio.gather(
            *(server._get_supplemental_client() for _ in range(10)))
    finally:
        await server._reset_supplemental_client()

    assert len(created) == 1
    assert all(client is clients[0] for client in clients)


@pytest.mark.asyncio
async def test_web_search_coalesces_identical_concurrent_misses(
        monkeypatch, isolated_search_state):
    calls = 0

    async def fake_execute(query, platform, model, extra_sources, mode,
                           session_id):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"session_id": session_id, "content": query, "sources_count": 0}

    monkeypatch.setattr(server, "_execute_web_search", fake_execute)

    results = await asyncio.gather(
        *(server.web_search("same query", extra_sources=0) for _ in range(10)))

    assert calls == 1
    assert all(result == results[0] for result in results)


@pytest.mark.asyncio
async def test_web_search_does_not_coalesce_different_keys(
        monkeypatch, isolated_search_state):
    calls = 0

    async def fake_execute(query, platform, model, extra_sources, mode,
                           session_id):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"session_id": session_id, "content": query, "sources_count": 0}

    monkeypatch.setattr(server, "_execute_web_search", fake_execute)

    first, second = await asyncio.gather(
        server.web_search("first", extra_sources=0),
        server.web_search("second", extra_sources=0),
    )

    assert calls == 2
    assert first["content"] == "first"
    assert second["content"] == "second"


@pytest.mark.asyncio
async def test_web_search_rechecks_cache_inside_single_flight(monkeypatch):
    cached = {"session_id": "cached", "content": "hit", "sources_count": 0}

    class CacheFilledDuringJoin(server.SearchResultCache):
        def __init__(self):
            super().__init__()
            self.reads = 0

        async def get(self, *args, **kwargs):
            self.reads += 1
            return None if self.reads == 1 else cached

    async def fail_execute(*args, **kwargs):
        raise AssertionError("upstream execution should be skipped")

    cache = CacheFilledDuringJoin()
    monkeypatch.setattr(server, "_RESULT_CACHE", cache)
    monkeypatch.setattr(server, "_WEB_SEARCH_SINGLE_FLIGHT",
                        server._SearchSingleFlight())
    monkeypatch.setattr(server, "_execute_web_search", fail_execute)

    assert await server.web_search("cache race", extra_sources=0) == cached
    assert cache.reads == 2


@pytest.mark.asyncio
async def test_web_search_cleans_single_flight_after_exception(
        monkeypatch, isolated_search_state):
    calls = 0

    async def fake_execute(query, platform, model, extra_sources, mode,
                           session_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("upstream failed")
        return {"session_id": session_id, "content": "retry", "sources_count": 0}

    monkeypatch.setattr(server, "_execute_web_search", fake_execute)

    with pytest.raises(RuntimeError, match="upstream failed"):
        await server.web_search("retry query", extra_sources=0)

    result = await server.web_search("retry query", extra_sources=0)
    assert calls == 2
    assert result["content"] == "retry"


@pytest.mark.asyncio
async def test_web_search_waiter_cancellation_does_not_cancel_shared_task(
        monkeypatch, isolated_search_state):
    started = asyncio.Event()
    release = asyncio.Event()
    cancelled = False

    async def fake_execute(query, platform, model, extra_sources, mode,
                           session_id):
        nonlocal cancelled
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled = True
            raise
        return {"session_id": session_id, "content": "done", "sources_count": 0}

    monkeypatch.setattr(server, "_execute_web_search", fake_execute)

    first = asyncio.create_task(server.web_search("shared", extra_sources=0))
    await started.wait()
    second = asyncio.create_task(server.web_search("shared", extra_sources=0))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    release.set()
    assert (await second)["content"] == "done"
    assert cancelled is False


@pytest.mark.asyncio
async def test_single_flight_consumes_orphaned_task_exception():
    single_flight = server._SearchSingleFlight()
    started = asyncio.Event()
    release = asyncio.Event()
    loop_errors = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))

    async def fail_after_cancellation():
        started.set()
        await release.wait()
        raise RuntimeError("background failure")

    waiter = asyncio.create_task(
        single_flight.run("orphan", fail_after_cancellation))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    release.set()
    try:
        for _ in range(3):
            await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert single_flight._tasks == {}
    assert loop_errors == []

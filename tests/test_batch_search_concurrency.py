import asyncio

import pytest

from grok_search import server


@pytest.mark.asyncio
async def test_batch_web_search_starts_distinct_queries_concurrently(monkeypatch):
    started = []
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def fake_cached_search(query, platform, model, extra_sources, mode):
        started.append(query)
        if len(started) == 4:
            all_started.set()
        await release.wait()
        return {"content": query, "sources_count": 0}

    monkeypatch.setattr(server, "_run_web_search_cached", fake_cached_search)

    task = asyncio.create_task(
        server.batch_web_search(
            ["q1", "q2", "q3", "q4"],
            extra_sources=0,
        )
    )

    await asyncio.wait_for(all_started.wait(), timeout=0.1)
    assert started == ["q1", "q2", "q3", "q4"]

    release.set()
    result = await task

    assert [item["content"] for item in result["results"]] == ["q1", "q2", "q3", "q4"]
    assert result["count"] == 4


@pytest.mark.asyncio
async def test_web_search_uses_shared_cached_execution_helper(monkeypatch):
    calls = []

    async def fake_cached_search(query, platform, model, extra_sources, mode):
        calls.append((query, platform, model, extra_sources, mode))
        return {"content": "ok", "sources_count": 0}

    monkeypatch.setattr(server, "_run_web_search_cached", fake_cached_search)

    result = await server.web_search("single", extra_sources=0)

    assert result["content"] == "ok"
    assert calls == [("single", "", "", 0, "balanced")]

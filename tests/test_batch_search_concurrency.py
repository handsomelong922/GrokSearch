import asyncio

import pytest

from grok_search import entrypoint


@pytest.mark.asyncio
async def test_batch_web_search_starts_distinct_queries_concurrently(monkeypatch):
    started = []
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def fake_web_search(query, platform="", model="", extra_sources=3, mode="balanced"):
        started.append(query)
        if len(started) == 4:
            all_started.set()
        await release.wait()
        return {"content": query, "sources_count": 0}

    monkeypatch.setattr(entrypoint.server, "web_search", fake_web_search)

    task = asyncio.create_task(
        entrypoint.batch_web_search(
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
async def test_batch_web_search_rejects_empty_queries(monkeypatch):
    result = await entrypoint.batch_web_search(["", "   "], extra_sources=0)

    assert result["count"] == 0
    assert result["results"] == []
    assert result["error"] == "no_valid_queries"

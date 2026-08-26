import asyncio
import json

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


@pytest.mark.asyncio
async def test_plan_execution_requires_batch_tool_for_parallel_searches(monkeypatch):
    server = entrypoint.server

    monkeypatch.setattr(server.planning_engine, "get_session", lambda _session_id: {"id": "p1"})
    monkeypatch.setattr(
        server.planning_engine,
        "process_phase",
        lambda **kwargs: {
            "session_id": kwargs["session_id"],
            "phase": kwargs["phase"],
            "data": kwargs["phase_data"],
        },
    )

    raw = await server.plan_execution(
        session_id="p1",
        thought="three independent searches can run together",
        parallel_groups="sq1,sq2,sq3",
        sequential="",
        estimated_rounds=1,
    )
    result = json.loads(raw)

    assert result["parallel_search_tool"] == "batch_web_search"
    assert "single MCP call" in result["parallel_search_instruction"]
    assert "Do not emit multiple web_search calls" in result["parallel_search_instruction"]


@pytest.mark.asyncio
async def test_plan_execution_does_not_force_batch_for_single_search(monkeypatch):
    server = entrypoint.server

    monkeypatch.setattr(server.planning_engine, "get_session", lambda _session_id: {"id": "p1"})
    monkeypatch.setattr(
        server.planning_engine,
        "process_phase",
        lambda **kwargs: {
            "session_id": kwargs["session_id"],
            "phase": kwargs["phase"],
            "data": kwargs["phase_data"],
        },
    )

    raw = await server.plan_execution(
        session_id="p1",
        thought="only one search",
        parallel_groups="sq1",
        sequential="",
        estimated_rounds=1,
    )
    result = json.loads(raw)

    assert "parallel_search_tool" not in result

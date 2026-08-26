import asyncio

import pytest

from grok_search import entrypoint
from grok_search.planning import PlanningEngine


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


def test_execution_plan_requires_batch_tool_for_parallel_searches():
    engine = PlanningEngine()
    result = engine.process_phase(
        phase="execution_order",
        thought="three independent searches can run together",
        session_id="p1",
        phase_data={
            "parallel": [["sq1", "sq2", "sq3"]],
            "sequential": [],
            "estimated_rounds": 1,
        },
    )

    assert result["parallel_search_tool"] == "batch_web_search"
    assert "single MCP call" in result["parallel_search_instruction"]
    assert "Do not emit multiple web_search calls" in result["parallel_search_instruction"]


def test_execution_plan_does_not_force_batch_for_single_search():
    engine = PlanningEngine()
    result = engine.process_phase(
        phase="execution_order",
        thought="only one search",
        session_id="p1",
        phase_data={
            "parallel": [["sq1"]],
            "sequential": [],
            "estimated_rounds": 1,
        },
    )

    assert "parallel_search_tool" not in result

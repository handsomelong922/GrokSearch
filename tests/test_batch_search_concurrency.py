import asyncio

import pytest

from grok_search import entrypoint
from grok_search.planning import PlanningEngine


@pytest.mark.asyncio
async def test_public_web_search_starts_distinct_queries_concurrently(monkeypatch):
    started = []
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def fake_single_search(query, platform="", model="", extra_sources=3, mode="balanced"):
        started.append(query)
        if len(started) == 4:
            all_started.set()
        await release.wait()
        return {"content": query, "sources_count": 0}

    monkeypatch.setattr(entrypoint, "_single_web_search", fake_single_search)

    task = asyncio.create_task(
        entrypoint.web_search(
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
async def test_public_web_search_supports_one_item_list(monkeypatch):
    calls = []

    async def fake_single_search(query, platform="", model="", extra_sources=3, mode="balanced"):
        calls.append(query)
        return {"content": query, "sources_count": 0}

    monkeypatch.setattr(entrypoint, "_single_web_search", fake_single_search)

    result = await entrypoint.web_search(["single query"], extra_sources=0)

    assert calls == ["single query"]
    assert result["count"] == 1
    assert result["results"][0]["content"] == "single query"


@pytest.mark.asyncio
async def test_public_web_search_rejects_empty_query_list(monkeypatch):
    result = await entrypoint.web_search(["", "   "], extra_sources=0)

    assert result["count"] == 0
    assert result["results"] == []
    assert result["error"] == "no_valid_queries"


def _add_web_mapping(engine: PlanningEngine, session_id: str, sub_query_id: str) -> None:
    engine.process_phase(
        phase="tool_selection",
        thought="use web search",
        session_id=session_id,
        phase_data={
            "sub_query_id": sub_query_id,
            "tool": "web_search",
            "reason": "needs web evidence",
        },
    )


def test_execution_plan_uses_one_array_web_search_for_parallel_searches():
    engine = PlanningEngine()
    for sub_query_id in ("sq1", "sq2", "sq3"):
        _add_web_mapping(engine, "p1", sub_query_id)

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

    assert result["parallel_search_tool"] == "web_search"
    assert "single MCP call" in result["parallel_search_instruction"]
    assert "query array" in result["parallel_search_instruction"]


def test_execution_plan_does_not_batch_non_search_parallel_tools():
    engine = PlanningEngine()
    engine.process_phase(
        phase="tool_selection",
        thought="fetch page",
        session_id="p1b",
        phase_data={"sub_query_id": "sq1", "tool": "web_fetch", "reason": "read page"},
    )
    engine.process_phase(
        phase="tool_selection",
        thought="map site",
        session_id="p1b",
        phase_data={"sub_query_id": "sq2", "tool": "web_map", "reason": "map site"},
    )

    result = engine.process_phase(
        phase="execution_order",
        thought="non-search tools can run together",
        session_id="p1b",
        phase_data={
            "parallel": [["sq1", "sq2"]],
            "sequential": [],
            "estimated_rounds": 1,
        },
    )

    assert "parallel_search_tool" not in result


def test_level_two_independent_web_search_mappings_use_array_web_search():
    engine = PlanningEngine()
    for sub_query in (
        {"id": "sq1", "goal": "first", "expected_output": "one", "boundary": "only first"},
        {"id": "sq2", "goal": "second", "expected_output": "two", "boundary": "only second"},
        {"id": "sq3", "goal": "third", "expected_output": "three", "boundary": "only third"},
    ):
        engine.process_phase(
            phase="query_decomposition",
            thought="independent sub-query",
            session_id="p2",
            phase_data=sub_query,
        )

    planning_result = engine.process_phase(
        phase="tool_selection",
        thought="set mappings",
        session_id="p2",
        is_revision=True,
        phase_data=[
            {"sub_query_id": "sq1", "tool": "web_search", "reason": "web"},
            {"sub_query_id": "sq2", "tool": "web_search", "reason": "web"},
            {"sub_query_id": "sq3", "tool": "web_search", "reason": "web"},
        ],
    )

    assert planning_result["parallel_search_tool"] == "web_search"
    assert "query array" in planning_result["parallel_search_instruction"]


def test_dependent_web_search_mappings_are_not_forced_into_one_array_call():
    engine = PlanningEngine()
    engine.process_phase(
        phase="query_decomposition",
        thought="first sub-query",
        session_id="p3",
        phase_data={
            "id": "sq1",
            "goal": "first",
            "expected_output": "one",
            "boundary": "only first",
        },
    )
    engine.process_phase(
        phase="query_decomposition",
        thought="second depends on first",
        session_id="p3",
        phase_data={
            "id": "sq2",
            "goal": "second",
            "expected_output": "two",
            "boundary": "only second",
            "depends_on": ["sq1"],
        },
    )
    _add_web_mapping(engine, "p3", "sq1")
    result = engine.process_phase(
        phase="tool_selection",
        thought="dependent web search",
        session_id="p3",
        phase_data={"sub_query_id": "sq2", "tool": "web_search", "reason": "second"},
    )

    assert "parallel_search_tool" not in result

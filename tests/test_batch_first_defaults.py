import inspect

from grok_search import entrypoint
from grok_search.planning import PlanningEngine


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


def test_batch_web_search_disables_supplemental_sources_by_default():
    signature = inspect.signature(entrypoint.batch_web_search)
    assert signature.parameters["extra_sources"].default == 0


def test_batch_web_search_accepts_single_query_as_primary_entrypoint():
    doc = inspect.getdoc(entrypoint.batch_web_search) or ""
    assert "one or more" in doc.lower() or "1-10" in doc.lower()


def test_planner_points_parallel_search_groups_to_batch_tool():
    engine = PlanningEngine()
    for sub_query_id in ("sq1", "sq2", "sq3"):
        _add_web_mapping(engine, "batch-first", sub_query_id)

    result = engine.process_phase(
        phase="execution_order",
        thought="run independent searches together",
        session_id="batch-first",
        phase_data={
            "parallel": [["sq1", "sq2", "sq3"]],
            "sequential": [],
            "estimated_rounds": 1,
        },
    )

    assert result["parallel_search_tool"] == "batch_web_search"
    assert "single MCP call" in result["parallel_search_instruction"]

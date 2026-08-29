import pytest

from grok_search import entrypoint


def test_batch_description_marks_batch_as_default_and_planning_optional():
    text = entrypoint.BATCH_WEB_SEARCH_DESCRIPTION.lower()
    assert "default" in text
    assert "do not call" in text
    assert "plan_search" in text


def test_plan_search_description_is_advanced_not_prerequisite():
    text = entrypoint.PLAN_SEARCH_DESCRIPTION.lower()
    assert "advanced" in text
    assert "not a prerequisite" in text
    assert "batch_web_search" in text


def test_legacy_planner_names_are_explicitly_hidden():
    assert entrypoint.LEGACY_PLANNER_TOOL_NAMES == (
        "plan_intent",
        "plan_complexity",
        "plan_sub_query",
        "plan_search_term",
        "plan_tool_mapping",
        "plan_execution",
    )


@pytest.mark.asyncio
async def test_plan_search_normalizes_web_search_to_batch_web_search():
    result = await entrypoint.plan_search(
        question="Compare two current software releases",
        complexity_level=2,
        sub_queries=[
            {"id": "sq1", "goal": "Find release A", "expected_output": "A", "boundary": "A only"},
            {"id": "sq2", "goal": "Find release B", "expected_output": "B", "boundary": "B only"},
        ],
        search_terms=[
            {"term": "release A latest", "purpose": "sq1", "round": 1},
            {"term": "release B latest", "purpose": "sq2", "round": 1},
        ],
        tool_mappings=[
            {"sub_query_id": "sq1", "tool": "web_search", "reason": "web evidence"},
            {"sub_query_id": "sq2", "tool": "batch_web_search", "reason": "web evidence"},
        ],
        parallel_groups=[["sq1", "sq2"]],
    )

    tools = [item["tool"] for item in result["executable_plan"]["tool_selection"]]
    assert tools == ["batch_web_search", "batch_web_search"]
    assert result["parallel_search_tool"] == "batch_web_search"
    assert result["plan_complete"] is True

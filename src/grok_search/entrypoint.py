"""Compatibility facade for the batch-first runtime entrypoint."""

from typing import Annotated

from . import runtime_entrypoint as _runtime

server = _runtime.server
BATCH_WEB_SEARCH_DESCRIPTION = _runtime.BATCH_WEB_SEARCH_DESCRIPTION
PLAN_SEARCH_DESCRIPTION = _runtime.PLAN_SEARCH_DESCRIPTION
LEGACY_PLANNER_TOOL_NAMES = _runtime.LEGACY_PLANNER_TOOL_NAMES
plan_search = _runtime.plan_search
get_config_info = _runtime.get_config_info
main = _runtime.main
reset_last_search_timing = _runtime.reset_last_search_timing
get_last_search_timing = _runtime.get_last_search_timing
_apply_persisted_provider_model_overrides = _runtime._apply_persisted_provider_model_overrides
_reset_provider_router = _runtime._reset_provider_router

# Keep these names patchable for historical tests/clients that import the Python
# module directly. The live MCP batch tool remains registered by runtime_entrypoint.
_single_web_search = _runtime._single_web_search


async def _run_batch(
    queries: list[str],
    platform: str = "",
    model: str = "",
    extra_sources: int = 0,
    mode: str = "balanced",
) -> dict:
    _runtime._single_web_search = _single_web_search
    return await _runtime._run_batch(queries, platform, model, extra_sources, mode)


async def switch_model(model: str, provider: str = "grok") -> str:
    """Python compatibility wrapper around the registered runtime tool."""
    _runtime._reset_provider_router = _reset_provider_router
    return await _runtime.switch_model(model=model, provider=provider)


# Replace only the compatibility web_search registration so historical scalar
# calls keep their exact result shape. New batch_web_search responses carry the
# additive telemetry fields.
try:
    server.mcp.local_provider.remove_tool("web_search")
except Exception:
    pass


@server.mcp.tool(
    name="web_search",
    output_schema=None,
    description="Backward-compatible search interface. New ChatGPT/MCP integrations should prefer batch_web_search, including for one-item lookups.",
    meta={"version": "3.2.0", "author": "guda.studio"},
)
async def web_search(
    query: Annotated[str | list[str], "A single query or independent query list."],
    platform: Annotated[str, "Optional target platform."] = "",
    model: Annotated[str, "Optional model override."] = "",
    extra_sources: Annotated[int, "Additional Tavily/Firecrawl references per query."] = 3,
    mode: Annotated[str, "fast, balanced, or deep."] = "balanced",
) -> dict:
    if isinstance(query, str):
        return await _single_web_search(
            query=query,
            platform=platform,
            model=model,
            extra_sources=extra_sources,
            mode=mode,
        )
    return await _run_batch(query, platform, model, extra_sources, mode)


async def batch_web_search(
    queries: list[str],
    platform: str = "",
    model: str = "",
    extra_sources: int = 0,
    mode: str = "balanced",
) -> dict:
    """Python compatibility wrapper; MCP registration lives in runtime_entrypoint."""
    return await _run_batch(queries, platform, model, extra_sources, mode)


if __name__ == "__main__":
    main()

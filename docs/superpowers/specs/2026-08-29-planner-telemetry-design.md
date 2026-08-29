# Planner Consolidation and Search Telemetry Design

## Scope

Keep `REASONING_EFFORT=high`. Do not implement Grok-first/Gemini grace-window routing in this change.

## Goals

1. Make `batch_web_search` the explicit default search tool for ordinary ChatGPT/MCP searches.
2. Remove the six legacy `plan_*` tools from the default public MCP schema while retaining their underlying planning engine for compatibility/internal reuse.
3. Add one public `plan_search` tool that returns a complete structured plan in one MCP round trip.
4. Ensure planner schemas consistently recognize `batch_web_search`.
5. Add additive latency/build telemetry that does not alter search-result semantics.

## Public MCP surface

Default public search/planning tools should include `batch_web_search` and `plan_search`; the old `plan_intent`, `plan_complexity`, `plan_sub_query`, `plan_search_term`, `plan_tool_mapping`, and `plan_execution` registrations are removed from the public provider at entrypoint startup. Their Python implementations remain in `server.py` for compatibility and future reuse.

`batch_web_search` description must say it is the default for ordinary factual, recent, comparative, and multi-query searches and that callers should not invoke planning first unless they explicitly need a structured multi-step research plan.

`plan_search` is an advanced planning helper, not a prerequisite. It accepts the user question plus optional planning depth/context and returns one structured object containing intent, complexity, subqueries, search terms, tool mappings, and execution order. The planner should prefer `batch_web_search` for independent web-search subqueries.

## Telemetry

Use `time.perf_counter()` only; no external dependency and no persistence required.

Each search result may add:

- `timing.total_ms`: end-to-end single-query time.
- `timing.providers_ms`: map of provider name to provider call duration.
- `timing.provider_router_ms`: router wall-clock duration.
- `timing.supplemental_ms`: Tavily/Firecrawl supplemental-search wall-clock duration when enabled, otherwise 0.
- `timing.postprocess_ms`: source merge/cache/result construction time.
- `cache_hit`: boolean.

Batch results may add:

- `batch_timing.total_ms`: batch wall-clock duration.
- `batch_timing.query_count`: number of accepted queries.

Provider timing is recorded inside `ProviderRouter` so parallel calls expose both individual durations and router wall-clock duration. Existing `content`, `sources_count`, `providers_used`, `supplementary`, `results`, and `count` fields remain unchanged.

## Runtime/build diagnostics

`get_config_info` should add a `runtime` object with safe, non-secret metadata when available: package version, git SHA/build version/docker tag environment values, Python version, provider strategy, and relevant timeout values. Missing build env vars are reported as empty/unknown rather than inferred.

## Compatibility and failure behavior

All new response fields are additive. Search execution, provider selection, cache key semantics, result ordering, and source extraction remain unchanged. Planner consolidation must not modify provider routing. If `plan_search` cannot fully classify a request, it should still return a valid conservative plan rather than trigger web search itself.

## Tests

Add regression tests for: default tool descriptions; six legacy planners absent from the public entrypoint tool list; `plan_search` present; planner tool hints allow `batch_web_search`; provider timing fields; single-search timing/cache-hit behavior; batch timing; runtime diagnostics; and existing batch/concurrency/model-switch tests remaining green.

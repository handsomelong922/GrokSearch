# Planner Consolidation and Search Telemetry Design

## Scope

Keep `REASONING_EFFORT=high`. Do not implement Grok-first/Gemini grace-window routing in this change.

## Goals

1. Make `batch_web_search` the explicit default search tool for ordinary ChatGPT/MCP searches.
2. Remove the six legacy `plan_*` tools from the default public MCP schema while retaining their underlying planning engine for compatibility/internal reuse.
3. Add one public `plan_search` tool that returns a complete structured plan in one MCP round trip.
4. Ensure the remaining public planner schema consistently recognizes and prefers `batch_web_search`.
5. Add additive latency/build telemetry without changing provider selection, search semantics, or reasoning effort.

## Public MCP surface

Default public search/planning tools include `batch_web_search` and `plan_search`; the old `plan_intent`, `plan_complexity`, `plan_sub_query`, `plan_search_term`, `plan_tool_mapping`, and `plan_execution` registrations are removed from the public provider at entrypoint startup. Their Python implementations remain in `server.py` for compatibility/internal reuse.

`batch_web_search` explicitly says it is the default for ordinary factual, recent, comparative, and multi-query searches and that callers should not plan first unless they explicitly need a structured multi-step research plan.

`plan_search` is an advanced planning helper, not a prerequisite. The host model supplies the decomposition in one MCP call; the server validates and normalizes it without making another LLM/web request. `web_search` mappings are normalized to `batch_web_search`, preserving server-side concurrency.

## Telemetry

Use `time.perf_counter()` only; no external dependency and no persistence required. Timing is implemented as a request-scoped wrapper around the existing provider/router classes rather than by rewriting routing behavior.

Each `batch_web_search` query result adds:

- `timing.total_ms`: end-to-end single-query wall-clock time.
- `timing.providers_ms`: map of Grok/Gemini provider call duration when the production OpenAI-compatible providers are used.
- `timing.provider_router_ms`: provider-router wall-clock duration.
- `timing.overhead_ms`: `total_ms - provider_router_ms`; this intentionally aggregates cache checks, optional supplemental search, source merging, and response construction.
- `timing.supplemental_enabled`: whether `extra_sources > 0` for that query.
- `cache_hit`: whether the result was already present in the result cache before execution.

Batch results add:

- `batch_timing.total_ms`: batch wall-clock duration.
- `batch_timing.query_count`: number of accepted queries.

The legacy scalar `web_search` keeps its historical response shape to minimize compatibility risk. Fine-grained Tavily/post-processing timers are deliberately deferred: if future measurements show `overhead_ms` is materially large, that is the evidence to justify instrumenting those deeper stages.

## Runtime/build diagnostics

`get_config_info` adds a safe `runtime` object containing package/Python version, provider strategy, reasoning effort, timeout values, telemetry version, and optional build identifiers. Docker builds inject `GIT_SHA`, `BUILD_VERSION`, and `DOCKER_TAG` from GitHub Actions so a deployed Space can report the exact image provenance without exposing secrets.

## Compatibility and failure behavior

Search execution, provider selection, result ordering, source extraction, cache keys, and Grok/Gemini waiting behavior remain unchanged. The telemetry wrapper is additive and request-scoped. Planner consolidation does not perform web I/O and does not alter provider routing. If `plan_search` receives only a question, it returns a conservative one-subquery batch-search plan.

## Tests and verification

Regression coverage targets: batch-first descriptions; `plan_search` normalization; legacy planner names hidden by the runtime entrypoint; legacy scalar `web_search` result shape; batch concurrency; additive timing/cache fields; runtime diagnostics; and existing provider/model/source behavior.

The current execution environment cannot install `fastmcp` because outbound DNS is unavailable, so full pytest execution must be performed in a normal development/CI environment before merge. Static source review and Python-only syntax checks are performed here, and the PR must not be merged on an unverified assumption.

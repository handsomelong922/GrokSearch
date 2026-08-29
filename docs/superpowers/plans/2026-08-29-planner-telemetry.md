# Planner Consolidation and Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `batch_web_search` the default low-latency search path, consolidate public planning to one `plan_search` tool, and add additive timing/build diagnostics without changing reasoning effort or provider routing.

**Architecture:** Keep the existing planning engine and legacy planner implementations internally, but remove their six public registrations in `entrypoint.py` and register one `plan_search` wrapper. Instrument provider calls in `providers/router.py`, propagate timing through the existing single-search result in `server.py`, add batch wall-clock timing in `entrypoint.py`, and expose safe runtime/build metadata through `get_config_info`.

**Tech Stack:** Python 3.12+, FastMCP, asyncio, Pydantic, pytest.

---

### Task 1: Lock the desired public tool surface with tests

**Files:**
- Modify: `tests/test_batch_first_defaults.py`
- Create: `tests/test_planner_consolidation.py`

- [ ] Add tests asserting `batch_web_search` description declares it the default and planning is not required first.
- [ ] Add tests asserting `plan_search` is registered by the entrypoint and the six legacy `plan_*` registrations are removed from the public provider.
- [ ] Add tests asserting planner schemas accept/prefer `batch_web_search`.
- [ ] Run the targeted tests and confirm they fail on the pre-change implementation.

### Task 2: Consolidate planner exposure

**Files:**
- Modify: `src/grok_search/entrypoint.py`
- Modify: `src/grok_search/planning.py`

- [ ] Remove six legacy planner registrations from `server.mcp.local_provider` only in the entrypoint compatibility layer.
- [ ] Add a pure planning helper that builds one complete structured plan from a question without performing web I/O.
- [ ] Register a public `plan_search` wrapper with a description that marks it advanced/optional and `batch_web_search` as the normal search path.
- [ ] Make all public/internal tool hints include `batch_web_search` consistently.
- [ ] Run planner/default tests and confirm they pass.

### Task 3: Add provider timing tests first

**Files:**
- Create: `tests/test_search_telemetry.py`

- [ ] Add an async test with fake providers that have controlled sleeps and assert per-provider `elapsed_ms` plus router wall-clock timing are populated.
- [ ] Add tests for single-search additive `timing`/`cache_hit` fields and batch-level `batch_timing`.
- [ ] Add a test that legacy result keys and ordering remain unchanged.
- [ ] Run telemetry tests and confirm they fail before implementation.

### Task 4: Implement low-overhead search telemetry

**Files:**
- Modify: `src/grok_search/providers/router.py`
- Modify: `src/grok_search/server.py`
- Modify: `src/grok_search/entrypoint.py`

- [ ] Extend `ProviderAnswer` with additive elapsed time and `SearchBatchResult` with timing metadata.
- [ ] Measure each provider call with `time.perf_counter()` and router wall-clock duration.
- [ ] Measure single-query total, supplemental, and post-processing durations and return them in `timing`.
- [ ] Return `cache_hit=true` for cache returns without changing cache-key behavior.
- [ ] Measure `_run_batch` wall-clock duration and return `batch_timing`.
- [ ] Run telemetry and concurrency tests.

### Task 5: Add runtime/build diagnostics

**Files:**
- Modify: `src/grok_search/entrypoint.py` or `src/grok_search/server.py`
- Extend: `tests/test_search_telemetry.py`

- [ ] Add safe runtime metadata: Python version, optional `GIT_SHA`/`BUILD_VERSION`/`DOCKER_TAG`, provider strategy, reasoning effort, and timeout values.
- [ ] Do not expose secrets and do not infer missing deployment metadata.
- [ ] Run diagnostics tests.

### Task 6: Regression verification

**Files:**
- No production changes unless a regression is found.

- [ ] Run the complete pytest suite.
- [ ] Verify existing batch concurrency, source extraction, provider-specific timeout, model switching, and request-option tests remain green.
- [ ] Inspect MCP schema expectations for backward compatibility.
- [ ] Confirm no code implementing Grok-first/Gemini grace-window behavior was added.

### Task 7: PR and handoff

**Files:**
- Update: `GrokSearch_Project_Handoff.md` after merge/deploy verification, not before production validation.

- [ ] Open a PR from `feat/planner-telemetry` to `main` with compatibility and telemetry notes.
- [ ] After merge, verify Docker workflow.
- [ ] After HF Space redeploy, reconnect/refresh ChatGPT MCP schema and live-test `get_config_info`, `plan_search`, one-item batch, and multi-item batch.

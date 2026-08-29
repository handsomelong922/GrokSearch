"""Runtime MCP registrations for batch-first GrokSearch."""

import asyncio
import importlib.metadata
import json
import os
import platform
import time
from typing import Annotated, Optional

from . import server
from .telemetry import (
    get_last_search_timing,
    install_search_telemetry,
    reset_last_search_timing,
)


BATCH_WEB_SEARCH_DESCRIPTION = """
DEFAULT web-search entrypoint for ChatGPT and other MCP hosts.

Use batch_web_search directly for ordinary factual, recent, comparative, and
multi-query searches. Run ONE OR MORE independent web searches in one MCP call;
a one-item list is the normal form for a single lookup and a multi-item list is
used for same-round research. Between 1 and 10 non-empty queries are accepted,
all items are started concurrently inside this server, and results preserve
input order.

Do NOT call plan_search before ordinary searches. plan_search is optional and
reserved for genuinely multi-step research where the caller wants a structured
plan. Supplemental Tavily/Firecrawl search is OFF by default
(`extra_sources=0`) to keep the normal path low-latency.
"""

PLAN_SEARCH_DESCRIPTION = """
Advanced optional research-planning tool. This is NOT a prerequisite for
batch_web_search and should not be called before ordinary searches.

Use it only when a genuinely multi-step research task benefits from an explicit
structured plan. The host model supplies the decomposition in this single MCP
call; the server validates and normalizes it without performing web I/O.
Independent web-search subqueries are normalized to batch_web_search so the
actual research can run with server-side concurrency.
"""

LEGACY_PLANNER_TOOL_NAMES = (
    "plan_intent",
    "plan_complexity",
    "plan_sub_query",
    "plan_search_term",
    "plan_tool_mapping",
    "plan_execution",
)


def _apply_persisted_provider_model_overrides() -> None:
    data = server.config._load_config_file()
    if not isinstance(data, dict):
        return
    grok_model = data.get("GROK_MODEL")
    gemini_model = data.get("GEMINI_MODEL")
    if isinstance(grok_model, str) and grok_model.strip():
        os.environ["GROK_MODEL"] = grok_model.strip()
        if hasattr(server.config, "_cached_model"):
            server.config._cached_model = None
    if isinstance(gemini_model, str) and gemini_model.strip():
        os.environ["GEMINI_MODEL"] = gemini_model.strip()


_apply_persisted_provider_model_overrides()
install_search_telemetry()


async def _reset_provider_router() -> None:
    from .providers import router as router_module
    async with router_module._router_lock:
        router_module._router = None


_single_web_search = server.web_search
_base_get_config_info = server.get_config_info


def _remove_public_tool(name: str) -> None:
    try:
        server.mcp.local_provider.remove_tool(name)
    except Exception:
        pass


for _tool_name in ("web_search", "switch_model", "get_config_info", *LEGACY_PLANNER_TOOL_NAMES):
    _remove_public_tool(_tool_name)


def _round_ms(seconds: float) -> float:
    return round(max(seconds, 0.0) * 1000.0, 2)


async def _run_single_instrumented(
    query: str,
    platform: str = "",
    model: str = "",
    extra_sources: int = 0,
    mode: str = "balanced",
) -> dict:
    started = time.perf_counter()
    cached_before = await server._RESULT_CACHE.get(query, platform, model, extra_sources, mode)
    reset_last_search_timing()
    result = await _single_web_search(
        query=query,
        platform=platform,
        model=model,
        extra_sources=extra_sources,
        mode=mode,
    )
    enriched = dict(result) if isinstance(result, dict) else {"content": str(result), "sources_count": 0}
    total_ms = _round_ms(time.perf_counter() - started)
    provider_timing = get_last_search_timing() or {}
    provider_router_ms = float(provider_timing.get("provider_router_ms", 0.0) or 0.0)
    enriched["timing"] = {
        "total_ms": total_ms,
        "provider_router_ms": round(provider_router_ms, 2),
        "providers_ms": dict(provider_timing.get("providers_ms", {}) or {}),
        "overhead_ms": round(max(total_ms - provider_router_ms, 0.0), 2),
        "supplemental_enabled": bool(extra_sources > 0),
    }
    enriched["cache_hit"] = cached_before is not None
    return enriched


async def _run_batch(
    queries: list[str],
    platform: str = "",
    model: str = "",
    extra_sources: int = 0,
    mode: str = "balanced",
) -> dict:
    batch_started = time.perf_counter()
    cleaned = [item.strip() for item in queries if isinstance(item, str) and item.strip()][:10]
    if not cleaned:
        return {
            "results": [],
            "count": 0,
            "error": "no_valid_queries",
            "batch_timing": {"total_ms": _round_ms(time.perf_counter() - batch_started), "query_count": 0},
        }

    async def _safe_one(item: str) -> dict:
        try:
            return await _run_single_instrumented(item, platform, model, extra_sources, mode)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {"content": "", "sources_count": 0, "error": f"query_error: {type(exc).__name__}: {exc}"}

    results = await asyncio.gather(*(_safe_one(item) for item in cleaned))
    return {
        "results": results,
        "count": len(results),
        "batch_timing": {
            "total_ms": _round_ms(time.perf_counter() - batch_started),
            "query_count": len(results),
        },
    }


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
        return await _run_single_instrumented(query, platform, model, extra_sources, mode)
    return await _run_batch(query, platform, model, extra_sources, mode)


@server.mcp.tool(
    name="batch_web_search",
    output_schema=None,
    description=BATCH_WEB_SEARCH_DESCRIPTION,
    meta={"version": "1.4.0", "author": "guda.studio"},
)
async def batch_web_search(
    queries: Annotated[list[str], "One to ten independent self-contained queries."],
    platform: Annotated[str, "Optional target platform applied to every query."] = "",
    model: Annotated[str, "Optional model override applied to every query."] = "",
    extra_sources: Annotated[int, "Additional Tavily/Firecrawl references per query. Default 0."] = 0,
    mode: Annotated[str, "fast, balanced, or deep."] = "balanced",
) -> dict:
    return await _run_batch(queries, platform, model, extra_sources, mode)


def _normalize_sub_queries(question: str, items: Optional[list[dict]]) -> list[dict]:
    output = []
    for index, item in enumerate(items or [], 1):
        if not isinstance(item, dict):
            continue
        tool_hint = item.get("tool_hint")
        if tool_hint in (None, "", "web_search", "batch_web_search"):
            tool_hint = "batch_web_search"
        output.append({
            "id": str(item.get("id") or f"sq{index}"),
            "goal": str(item.get("goal") or question),
            "expected_output": str(item.get("expected_output") or "Evidence-grounded answer"),
            "boundary": str(item.get("boundary") or "Keep this sub-query distinct from siblings"),
            "depends_on": list(item.get("depends_on") or []),
            "tool_hint": tool_hint,
        })
    return output or [{
        "id": "sq1",
        "goal": question,
        "expected_output": "Evidence-grounded answer",
        "boundary": "Answer only the requested question",
        "depends_on": [],
        "tool_hint": "batch_web_search",
    }]


def _normalize_search_terms(question: str, items: Optional[list[dict]], sub_queries: list[dict]) -> list[dict]:
    output = []
    for item in items or []:
        if not isinstance(item, dict) or not str(item.get("term") or "").strip():
            continue
        output.append({
            "term": " ".join(str(item["term"]).split()[:8]),
            "purpose": str(item.get("purpose") or sub_queries[0]["id"]),
            "round": max(int(item.get("round") or 1), 1),
        })
    return output or [{"term": " ".join(question.split()[:8]), "purpose": sub_queries[0]["id"], "round": 1}]


def _normalize_tool_mappings(items: Optional[list[dict]], sub_queries: list[dict]) -> list[dict]:
    output = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "batch_web_search")
        if tool == "web_search" or tool not in {"batch_web_search", "web_fetch", "web_map"}:
            tool = "batch_web_search"
        mapped = {
            "sub_query_id": str(item.get("sub_query_id") or sub_queries[0]["id"]),
            "tool": tool,
            "reason": str(item.get("reason") or "Use the most direct evidence tool"),
        }
        if isinstance(item.get("params"), dict):
            mapped["params"] = item["params"]
        output.append(mapped)
    return output or [
        {"sub_query_id": item["id"], "tool": "batch_web_search", "reason": "Default web research path"}
        for item in sub_queries
    ]


@server.mcp.tool(
    name="plan_search",
    output_schema=None,
    description=PLAN_SEARCH_DESCRIPTION,
    meta={"version": "1.0.0", "author": "guda.studio"},
)
async def plan_search(
    question: Annotated[str, "Research question to structure."],
    complexity_level: Annotated[int, "1=simple, 2=moderate, 3=complex."] = 1,
    sub_queries: Annotated[Optional[list[dict]], "Optional complete sub-query list from the host model."] = None,
    search_terms: Annotated[Optional[list[dict]], "Optional search-term list."] = None,
    tool_mappings: Annotated[Optional[list[dict]], "Optional tool mappings; web_search is normalized to batch_web_search."] = None,
    parallel_groups: Annotated[Optional[list[list[str]]], "Optional groups of independent sub-query IDs."] = None,
    sequential: Annotated[Optional[list[str]], "Optional ordered dependent sub-query IDs."] = None,
    approach: Annotated[str, "broad_first, narrow_first, or targeted."] = "targeted",
    fallback_plan: Annotated[str, "Optional fallback if primary searches fail."] = "",
) -> dict:
    question = (question or "").strip()
    if not question:
        return {"error": "empty_question", "plan_complete": False}
    if complexity_level not in (1, 2, 3):
        return {"error": "invalid_complexity_level", "plan_complete": False}
    if approach not in {"broad_first", "narrow_first", "targeted"}:
        approach = "targeted"
    sub_queries_n = _normalize_sub_queries(question, sub_queries)
    terms_n = _normalize_search_terms(question, search_terms, sub_queries_n)
    mappings_n = _normalize_tool_mappings(tool_mappings, sub_queries_n)
    if parallel_groups is None:
        independent = [item["id"] for item in sub_queries_n if not item.get("depends_on")]
        parallel_groups = [independent] if independent else []
    sequential = list(sequential or [])
    return {
        "plan_complete": True,
        "complexity_level": complexity_level,
        "parallel_search_tool": "batch_web_search",
        "parallel_search_instruction": "Execute independent web-search sub-queries with one batch_web_search call; do not emit separate same-round search calls.",
        "executable_plan": {
            "intent_analysis": {"core_question": question},
            "complexity_assessment": {"level": complexity_level, "estimated_sub_queries": len(sub_queries_n)},
            "query_decomposition": sub_queries_n,
            "search_strategy": {
                "approach": approach,
                "search_terms": terms_n,
                **({"fallback_plan": fallback_plan} if fallback_plan else {}),
            },
            "tool_selection": mappings_n,
            "execution_order": {
                "parallel": parallel_groups,
                "sequential": sequential,
                "estimated_rounds": max(1, 1 + len(sequential)),
            },
        },
    }


def _runtime_metadata(config_data: dict) -> dict:
    try:
        package_version = importlib.metadata.version("grok-search")
    except importlib.metadata.PackageNotFoundError:
        package_version = "unknown"
    return {
        "package_version": package_version,
        "python_version": platform.python_version(),
        "git_sha": os.getenv("GIT_SHA", ""),
        "build_version": os.getenv("BUILD_VERSION", os.getenv("GITHUB_RUN_NUMBER", "")),
        "docker_tag": os.getenv("DOCKER_TAG", ""),
        "provider_strategy": config_data.get("SEARCH_PROVIDER_STRATEGY", os.getenv("SEARCH_PROVIDER_STRATEGY", "parallel")),
        "reasoning_effort": config_data.get("REASONING_EFFORT", os.getenv("REASONING_EFFORT", "high")),
        "telemetry_version": 1,
        "timeouts_seconds": {
            "provider_outer": float(os.getenv("WEB_SEARCH_GROK_TIMEOUT_SECONDS", "80")),
            "grok_stream_read": float(os.getenv("GROK_HTTP_READ_TIMEOUT_SECONDS", "10")),
            "gemini_stream_read": float(os.getenv("GEMINI_HTTP_READ_TIMEOUT_SECONDS", "15")),
            "tavily_search": float(os.getenv("WEB_SEARCH_TAVILY_TIMEOUT_SECONDS", "30")),
            "firecrawl_search": float(os.getenv("WEB_SEARCH_FIRECRAWL_TIMEOUT_SECONDS", "30")),
        },
    }


@server.mcp.tool(
    name="get_config_info",
    output_schema=None,
    description="Return current GrokSearch configuration, connectivity checks, and safe runtime/build diagnostics.",
    meta={"version": "1.4.0", "author": "guda.studio"},
)
async def get_config_info() -> str:
    raw = await _base_get_config_info()
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        data = {"config_raw": str(raw)}
    data["runtime"] = _runtime_metadata(data)
    return json.dumps(data, ensure_ascii=False, indent=2)


@server.mcp.tool(
    name="switch_model",
    output_schema=None,
    description="Switch and persist the model for Grok or Gemini. Calls that provide only model remain backward-compatible and target Grok.",
    meta={"version": "2.0.0", "author": "guda.studio"},
)
async def switch_model(
    model: Annotated[str, "Model ID to activate for the selected provider."],
    provider: Annotated[str, "Target provider: grok (default) or gemini."] = "grok",
) -> str:
    target = (provider or "grok").strip().lower()
    requested_model = (model or "").strip()
    if target not in {"grok", "gemini"}:
        return json.dumps({"status": "❌ 失败", "error": "unsupported_provider", "message": "provider 必须是 grok 或 gemini"}, ensure_ascii=False, indent=2)
    if not requested_model:
        return json.dumps({"status": "❌ 失败", "error": "empty_model", "message": "model 不能为空"}, ensure_ascii=False, indent=2)

    if target == "grok":
        provider_name = "Grok"
        api_url, api_key, previous_model = server.config.grok_api_url, server.config.grok_api_key, server.config.grok_model
    else:
        provider_name = "Gemini"
        api_url, api_key, previous_model = server.config.gemini_api_url, server.config.gemini_api_key, server.config.gemini_model
        if not api_url or not api_key:
            return json.dumps({"status": "❌ 失败", "provider": provider_name, "error": "provider_not_configured", "message": "Gemini provider 未配置 GEMINI_API_URL/GEMINI_API_KEY"}, ensure_ascii=False, indent=2)

    available_models = await server._get_available_models_cached(api_url, api_key)
    if available_models and requested_model not in available_models:
        return json.dumps({
            "status": "❌ 失败",
            "provider": provider_name,
            "error": "model_not_available",
            "requested_model": requested_model,
            "available_models": available_models,
            "message": f"{requested_model} 不在 {provider_name} 当前模型列表中",
        }, ensure_ascii=False, indent=2)

    config_data = server.config._load_config_file()
    if not isinstance(config_data, dict):
        config_data = {}
    if target == "grok":
        config_data["model"] = requested_model
        config_data["GROK_MODEL"] = requested_model
    else:
        config_data["GEMINI_MODEL"] = requested_model
    try:
        server.config._save_config_file(config_data)
    except ValueError as exc:
        return json.dumps({"status": "❌ 失败", "provider": provider_name, "error": "persist_failed", "message": f"切换模型失败: {exc}"}, ensure_ascii=False, indent=2)

    os.environ["GROK_MODEL" if target == "grok" else "GEMINI_MODEL"] = requested_model
    if target == "grok" and hasattr(server.config, "_cached_model"):
        server.config._cached_model = None
    await _reset_provider_router()
    return json.dumps({
        "status": "✅ 成功",
        "provider": provider_name,
        "previous_model": previous_model,
        "current_model": requested_model,
        "validation": "validated" if available_models else "catalog_unavailable_skipped",
        "message": f"{provider_name} 模型已从 {previous_model} 切换到 {requested_model}",
        "config_file": str(server.config.config_file),
    }, ensure_ascii=False, indent=2)


def main():
    server.main()

"""Application entrypoint with backward-compatible search concurrency.

The base server owns the fully featured single-query search pipeline. This
module keeps that implementation as an internal helper, replaces selected
public registrations with compatibility wrappers, and keeps batch search as the
preferred search entrypoint.
"""

import asyncio
import json
import os
from typing import Annotated

from . import server


def _apply_persisted_provider_model_overrides() -> None:
    """Apply model overrides written by switch_model before routers initialize.

    Deployment environment variables remain the baseline configuration. Only
    explicit provider keys written by the provider-aware switch tool override
    them, so legacy config files containing only ``model`` do not unexpectedly
    change deployment behavior after an upgrade.
    """
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


async def _reset_provider_router() -> None:
    """Drop the lazy provider router so the next search sees new model config."""
    from .providers import router as router_module

    async with router_module._router_lock:
        router_module._router = None


# Preserve the existing single-query implementation as the canonical internal
# execution path. Public wrappers below only normalize MCP input shapes.
_single_web_search = server.web_search

# Replace the original public registration so ``web_search`` can accept both
# the historical scalar form and the newer list form without changing the
# underlying single-query implementation.
server.mcp.local_provider.remove_tool("web_search")

# Replace the original Grok-only model switch with a provider-aware wrapper.
# Keeping the same public tool name and model-first signature preserves old
# clients that only send {"model": "..."}.
server.mcp.local_provider.remove_tool("switch_model")


async def _run_batch(
    queries: list[str],
    platform: str = "",
    model: str = "",
    extra_sources: int = 0,
    mode: str = "balanced",
) -> dict:
    """Execute one to ten independent queries concurrently, preserving order."""
    cleaned = [item.strip() for item in queries if isinstance(item, str) and item.strip()]
    if not cleaned:
        return {"results": [], "count": 0, "error": "no_valid_queries"}

    cleaned = cleaned[:10]

    async def _safe_one(item: str) -> dict:
        try:
            result = await _single_web_search(
                query=item,
                platform=platform,
                model=model,
                extra_sources=extra_sources,
                mode=mode,
            )
            if isinstance(result, dict):
                return result
            return {"content": str(result), "sources_count": 0}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {
                "content": "",
                "sources_count": 0,
                "error": f"query_error: {type(exc).__name__}: {exc}",
            }

    results = await asyncio.gather(*(_safe_one(item) for item in cleaned))
    return {"results": results, "count": len(results)}


@server.mcp.tool(
    name="web_search",
    output_schema=None,
    description="""
    Search the web using either the legacy single-query form or a concurrent
    multi-query form.

    Backward compatibility:
    - `query` may be a STRING. This behaves exactly like the historical
      web_search tool and returns the historical single-search result shape.
    - `query` may be an ARRAY of strings. Independent queries in that array are
      started concurrently inside this server with asyncio.gather.

    For two or more independent searches in the same execution round, prefer a
    single array call instead of multiple separate web_search calls, because
    some MCP hosts serialize separate calls.
    """,
    meta={
        "version": "3.1.0",
        "author": "guda.studio",
    },
)
async def web_search(
    query: Annotated[
        str | list[str],
        "A single search query string, or an array of independent queries to run concurrently."
    ],
    platform: Annotated[
        str,
        "Optional target platform. Leave empty for general web search."
    ] = "",
    model: Annotated[
        str,
        "Optional model override."
    ] = "",
    extra_sources: Annotated[
        int,
        "Number of additional Tavily/Firecrawl reference results per query. Set 0 to disable."
    ] = 3,
    mode: Annotated[
        str,
        "Search mode: fast, balanced, or deep."
    ] = "balanced",
) -> dict:
    if isinstance(query, str):
        return await _single_web_search(
            query=query,
            platform=platform,
            model=model,
            extra_sources=extra_sources,
            mode=mode,
        )

    return await _run_batch(
        queries=query,
        platform=platform,
        model=model,
        extra_sources=extra_sources,
        mode=mode,
    )


@server.mcp.tool(
    name="batch_web_search",
    output_schema=None,
    description="""
    Preferred web-search entrypoint for ChatGPT and other MCP hosts.

    Run ONE OR MORE independent web searches in one MCP call. Use a one-item
    list for a single lookup and a multi-item list for same-round research.
    Between 1 and 10 non-empty queries are accepted; all items are started
    concurrently inside this server and results preserve input order.

    Latency policy: supplemental Tavily/Firecrawl search is OFF by default
    (`extra_sources=0`). Set `extra_sources` above zero only when additional
    source discovery is explicitly worth the extra tail latency. This does not
    disable web_fetch or web_map; those remain available as separate on-demand
    tools when configured.
    """,
    meta={
        "version": "1.3.0",
        "author": "guda.studio",
    },
)
async def batch_web_search(
    queries: Annotated[
        list[str],
        "One to ten independent, self-contained search queries to execute concurrently."
    ],
    platform: Annotated[
        str,
        "Optional target platform applied to every query."
    ] = "",
    model: Annotated[
        str,
        "Optional model override applied to every query."
    ] = "",
    extra_sources: Annotated[
        int,
        "Additional Tavily/Firecrawl references per query. Default 0 for lowest latency; opt in with a positive value."
    ] = 0,
    mode: Annotated[
        str,
        "Search mode applied to every query: fast, balanced, or deep."
    ] = "balanced",
) -> dict:
    return await _run_batch(
        queries=queries,
        platform=platform,
        model=model,
        extra_sources=extra_sources,
        mode=mode,
    )


@server.mcp.tool(
    name="switch_model",
    output_schema=None,
    description="""
    Switch and persist the model for a specific search provider.

    Backward compatibility:
    - Existing calls that provide only `model` still target Grok.
    - Set `provider="gemini"` to change the Gemini secondary provider.

    The selected model is checked against that provider's /models catalog when
    available, saved in ~/.config/grok-search/config.json, applied immediately
    to the current process, and used by newly created provider routers.
    """,
    meta={
        "version": "2.0.0",
        "author": "guda.studio",
    },
)
async def switch_model(
    model: Annotated[str, "Model ID to activate for the selected provider."],
    provider: Annotated[
        str,
        "Target provider: 'grok' (default, backward compatible) or 'gemini'."
    ] = "grok",
) -> str:
    target = (provider or "grok").strip().lower()
    requested_model = (model or "").strip()

    if target not in {"grok", "gemini"}:
        return json.dumps(
            {
                "status": "❌ 失败",
                "error": "unsupported_provider",
                "message": "provider 必须是 grok 或 gemini",
            },
            ensure_ascii=False,
            indent=2,
        )

    if not requested_model:
        return json.dumps(
            {
                "status": "❌ 失败",
                "error": "empty_model",
                "message": "model 不能为空",
            },
            ensure_ascii=False,
            indent=2,
        )

    if target == "grok":
        provider_name = "Grok"
        api_url = server.config.grok_api_url
        api_key = server.config.grok_api_key
        previous_model = server.config.grok_model
    else:
        provider_name = "Gemini"
        api_url = server.config.gemini_api_url
        api_key = server.config.gemini_api_key
        previous_model = server.config.gemini_model
        if not api_url or not api_key:
            return json.dumps(
                {
                    "status": "❌ 失败",
                    "provider": provider_name,
                    "error": "provider_not_configured",
                    "message": "Gemini provider 未配置 GEMINI_API_URL/GEMINI_API_KEY",
                },
                ensure_ascii=False,
                indent=2,
            )

    available_models = await server._get_available_models_cached(api_url, api_key)
    if available_models and requested_model not in available_models:
        return json.dumps(
            {
                "status": "❌ 失败",
                "provider": provider_name,
                "error": "model_not_available",
                "requested_model": requested_model,
                "available_models": available_models,
                "message": f"{requested_model} 不在 {provider_name} 当前模型列表中",
            },
            ensure_ascii=False,
            indent=2,
        )

    config_data = server.config._load_config_file()
    if not isinstance(config_data, dict):
        config_data = {}

    if target == "grok":
        # Keep the legacy field for older versions while adding an explicit
        # provider key used by the new startup override logic.
        config_data["model"] = requested_model
        config_data["GROK_MODEL"] = requested_model
    else:
        config_data["GEMINI_MODEL"] = requested_model

    try:
        server.config._save_config_file(config_data)
    except ValueError as exc:
        return json.dumps(
            {
                "status": "❌ 失败",
                "provider": provider_name,
                "error": "persist_failed",
                "message": f"切换模型失败: {exc}",
            },
            ensure_ascii=False,
            indent=2,
        )

    env_key = "GROK_MODEL" if target == "grok" else "GEMINI_MODEL"
    os.environ[env_key] = requested_model
    if target == "grok" and hasattr(server.config, "_cached_model"):
        server.config._cached_model = None

    await _reset_provider_router()

    return json.dumps(
        {
            "status": "✅ 成功",
            "provider": provider_name,
            "previous_model": previous_model,
            "current_model": requested_model,
            "validation": "validated" if available_models else "catalog_unavailable_skipped",
            "message": f"{provider_name} 模型已从 {previous_model} 切换到 {requested_model}",
            "config_file": str(server.config.config_file),
        },
        ensure_ascii=False,
        indent=2,
    )


def main():
    server.main()


if __name__ == "__main__":
    main()

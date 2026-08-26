"""Application entrypoint with backward-compatible search concurrency.

The base server owns the fully featured single-query search pipeline. This
module keeps that implementation as an internal helper, replaces the public
``web_search`` registration with a compatibility wrapper that accepts either a
legacy string query or a list of queries, and also keeps ``batch_web_search``
available as the preferred batch-first entrypoint.
"""

import asyncio
from typing import Annotated

from . import server


# Preserve the existing single-query implementation as the canonical internal
# execution path. Public wrappers below only normalize MCP input shapes.
_single_web_search = server.web_search

# Replace the original public registration so ``web_search`` can accept both
# the historical scalar form and the newer list form without changing the
# underlying single-query implementation.
server.mcp.local_provider.remove_tool("web_search")


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


def main():
    server.main()


if __name__ == "__main__":
    main()

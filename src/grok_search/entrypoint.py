"""Application entrypoint with backward-compatible search concurrency.

The base server owns the fully featured single-query search pipeline. This
module keeps that implementation as an internal helper, replaces the public
``web_search`` registration with a compatibility wrapper that accepts either a
legacy string query or a list of queries, and also keeps ``batch_web_search``
available for clients that already know that tool name.
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
    extra_sources: int = 3,
    mode: str = "balanced",
) -> dict:
    """Execute independent queries concurrently while preserving input order."""
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
    single array call such as ["query A", "query B"] instead of multiple
    separate web_search calls, because some MCP hosts serialize separate calls.
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
    Backward-compatible batch search alias.

    Run two or more independent web searches concurrently inside one MCP call.
    Existing clients that already call batch_web_search can continue using this
    tool unchanged. Results preserve input order.
    """,
    meta={
        "version": "1.2.0",
        "author": "guda.studio",
    },
)
async def batch_web_search(
    queries: Annotated[
        list[str],
        "Independent, self-contained search queries to execute concurrently."
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
        "Additional Tavily/Firecrawl references per query. Set 0 to disable."
    ] = 3,
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

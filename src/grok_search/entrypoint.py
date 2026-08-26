"""Application entrypoint with additional MCP tools.

The base server keeps the single-query ``web_search`` tool. This module adds a
batch tool so multiple independent queries can be started concurrently inside
one MCP request, avoiding client-side tool-call serialization.
"""

import asyncio
from typing import Annotated

from . import server


@server.mcp.tool(
    name="batch_web_search",
    output_schema=None,
    description="""
    Run multiple independent web searches concurrently in one MCP call.

    Prefer this tool when two or more independent sub-queries can be searched
    at the same time. All queries are started concurrently on the server with
    asyncio.gather, so parallelism does not depend on the MCP client issuing
    multiple web_search tool calls concurrently.

    Returns results in the same order as the input queries.
    """,
    meta={
        "version": "1.0.0",
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
        "Optional target platform applied to every query. Leave empty for general web search."
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
    cleaned = [query.strip() for query in queries if isinstance(query, str) and query.strip()]
    if not cleaned:
        return {"results": [], "count": 0, "error": "no_valid_queries"}

    # Bound a single batch to avoid accidental fan-out while still covering
    # normal multi-query research workloads.
    max_batch = 10
    cleaned = cleaned[:max_batch]

    async def _safe_one(query: str) -> dict:
        try:
            result = await server.web_search(
                query=query,
                platform=platform,
                model=model,
                extra_sources=extra_sources,
                mode=mode,
            )
            if isinstance(result, dict):
                return result
            return {
                "content": str(result),
                "sources_count": 0,
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {
                "content": "",
                "sources_count": 0,
                "error": f"batch_query_error: {type(exc).__name__}: {exc}",
            }

    results = await asyncio.gather(*(_safe_one(query) for query in cleaned))
    return {
        "results": results,
        "count": len(results),
    }


def main():
    server.main()


if __name__ == "__main__":
    main()

"""Application entrypoint with server-controlled search concurrency.

The base server implements the single-query search pipeline. At startup this
module removes that scalar MCP tool from the public tool list and exposes one
array-based ``web_search`` tool instead. This keeps multi-query concurrency
inside the server rather than relying on an MCP host to execute separate tool
calls concurrently.
"""

import asyncio
from typing import Annotated

from . import server


# Keep the existing, fully featured single-query implementation as an internal
# helper before replacing its public MCP registration.
_single_web_search = server.web_search

# FastMCP >=2.3.4 supports removing tools through the local provider. The
# project installs current FastMCP releases, and doing this at startup means
# clients only discover the unified array-based web_search below.
server.mcp.local_provider.remove_tool("web_search")


@server.mcp.tool(
    name="web_search",
    output_schema=None,
    description="""
    Search the web for one or more independent queries.

    IMPORTANT CONCURRENCY CONTRACT:
    - The `query` argument is ALWAYS an array of strings.
    - For one search, send one item: ["query"].
    - For two or more independent searches needed in the same execution round,
      put ALL of them in ONE `web_search` call: ["query A", "query B", ...].
    - Do NOT emit multiple separate `web_search` tool calls for independent
      searches. Some MCP hosts serialize separate tool calls.

    This server starts every query in the array concurrently with
    `asyncio.gather`. Each query still runs the existing Grok/Gemini provider
    pipeline, cache, timeout, source collection, and fallback behavior.

    Returns `results` in the same order as the input query array.
    """,
    meta={
        "version": "3.0.0",
        "author": "guda.studio",
    },
)
async def web_search(
    query: Annotated[
        list[str],
        "Array of self-contained search queries. Put all independent same-round searches in this one array."
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
        "Number of additional Tavily/Firecrawl reference results per query. Set 0 to disable."
    ] = 3,
    mode: Annotated[
        str,
        "Search mode applied to every query: fast, balanced, or deep."
    ] = "balanced",
) -> dict:
    cleaned = [item.strip() for item in query if isinstance(item, str) and item.strip()]
    if not cleaned:
        return {"results": [], "count": 0, "error": "no_valid_queries"}

    # Bound one request to prevent accidental unbounded fan-out while allowing
    # normal research batches to execute fully in parallel.
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


def main():
    server.main()


if __name__ == "__main__":
    main()

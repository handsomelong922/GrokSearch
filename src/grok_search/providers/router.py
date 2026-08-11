"""
Provider Router - manages multiple search providers and routing strategies.

Supports routing strategies:
- parallel: Run all providers concurrently, merge results
- fallback: Try providers in order, fall back on failure
- primary: Use only the primary provider (Grok)
"""

import asyncio
import os
from typing import Optional

from .base import BaseSearchProvider
from .openai_compatible import OpenAICompatibleSearchProvider
from ..config import config
from ..sources import merge_sources, split_answer_and_sources
from ..logger import log_info
from ..dedup import extract_supplementary


class ProviderAnswer:
    """Result from a single provider."""
    def __init__(self, provider_name: str, content: str, error: Optional[str] = None):
        self.provider_name = provider_name
        self.content = content
        self.error = error


class SearchBatchResult:
    """Merged result from all providers."""
    def __init__(self):
        self.answers: list[ProviderAnswer] = []
        self.primary_content: str = ""
        self.all_sources: list[dict] = []
        self.errors: list[str] = []
        self.providers_used: list[str] = []
        self.supplementary: str = ""

    def get_provider_names(self) -> list[str]:
        """Return names of providers that produced content or errors."""
        names = set()
        for answer in self.answers:
            if answer.provider_name:
                names.add(answer.provider_name)
        return sorted(names)


class ProviderRouter:
    """Routes search requests to configured providers based on strategy."""

    def __init__(self):
        self._providers: list[BaseSearchProvider] = []
        self._initialized = False

    def _ensure_initialized(self):
        """Initialize provider instances from config."""
        if self._initialized:
            return
        self._providers = []
        for provider_cfg in config.get_search_providers():
            provider = OpenAICompatibleSearchProvider(
                api_url=provider_cfg["api_url"],
                api_key=provider_cfg["api_key"],
                model=provider_cfg["model"],
                provider_name=provider_cfg["name"],
            )
            self._providers.append(provider)
        self._initialized = True

    def get_providers(self) -> list[BaseSearchProvider]:
        """Return all configured provider instances."""
        self._ensure_initialized()
        return list(self._providers)

    def get_primary_provider(self) -> Optional[BaseSearchProvider]:
        """Return the primary provider (first configured)."""
        providers = self.get_providers()
        return providers[0] if providers else None

    def get_provider_names(self) -> list[str]:
        """Return names of all configured providers."""
        return [p.get_provider_name() for p in self.get_providers()]

    async def run_search(
        self,
        query: str,
        platform: str = "",
        mode: str = "balanced",
        model_override: str = "",
        ctx=None,
    ) -> SearchBatchResult:
        """Run search across all providers according to the configured strategy.

        Strategy is read from config.search_provider_strategy:
        - "parallel": Run all providers concurrently, merge results
        - "fallback": Try providers in order
        - "primary": Use only the primary provider

        Args:
            model_override: If set, overrides the model for the primary provider (Grok).
                           Gemini always uses its own configured model.
        """
        strategy = config.search_provider_strategy
        providers = self.get_providers()
        if model_override and providers:
            # Create a temporary primary provider with the overridden model
            cfgs = config.get_search_providers()
            if not cfgs:
                result = SearchBatchResult()
                result.errors.append("没有可用的搜索 Provider")
                return result
            primary_cfg = cfgs[0]
            providers[0] = OpenAICompatibleSearchProvider(
                api_url=primary_cfg["api_url"],
                api_key=primary_cfg["api_key"],
                model=model_override,
                provider_name=primary_cfg["name"],
            )

        if not providers:
            result = SearchBatchResult()
            result.errors.append("没有可用的搜索 Provider")
            return result

        if strategy == "parallel":
            return await self._run_parallel(query, platform, mode, providers, ctx)
        elif strategy == "fallback":
            return await self._run_fallback(query, platform, mode, providers, ctx)
        else:  # primary (default)
            return await self._run_primary(query, platform, mode, providers, ctx)

    async def _run_parallel(
        self,
        query: str,
        platform: str,
        mode: str,
        providers: list[BaseSearchProvider],
        ctx=None,
    ) -> SearchBatchResult:
        """Run all providers in parallel, merge results.

        Merge strategy (Plan B):
        1. Run all providers in parallel
        2. Identify primary provider (configurable, default: Grok)
        3. Primary provider answer goes to `content`
        4. Secondary provider: extract supplementary (sentences not in primary)
        5. Merge sources from both (deduplicate by URL)
        """
        result = SearchBatchResult()
        if not providers:
            result.errors.append("没有可用的搜索 Provider")
            return result

        timeout = float(os.getenv("WEB_SEARCH_GROK_TIMEOUT_SECONDS", "120"))
        primary_name = config.search_provider_primary.capitalize()

        async def _safe_call(provider: BaseSearchProvider) -> ProviderAnswer:
            name = provider.get_provider_name()
            try:
                content = await asyncio.wait_for(
                    provider.search(query, platform, mode=mode),
                    timeout=timeout,
                )
                return ProviderAnswer(name, content)
            except asyncio.TimeoutError:
                return ProviderAnswer(name, "", f"{name.lower()}_timeout")
            except Exception as e:
                return ProviderAnswer(name, "", f"{name.lower()}_error: {type(e).__name__}: {e}")

        # Run all providers concurrently
        answers = await asyncio.gather(*[_safe_call(p) for p in providers])
        result.answers = answers
        result.providers_used = [a.provider_name for a in answers if a.provider_name]

        # Parse each answer into content + sources
        all_contents: dict[str, tuple[str, str, list[dict]]] = {}
        all_sources_lists: list[list[dict]] = []

        for answer in answers:
            if answer.error:
                result.errors.append(answer.error)
            else:
                content, sources = split_answer_and_sources(answer.content)
                all_contents[answer.provider_name] = (answer.provider_name, content, sources)
                all_sources_lists.append(sources)

        # Merge sources (deduplicate by URL)
        result.all_sources = merge_sources(*all_sources_lists) if all_sources_lists else []

        # Plan B: primary provider determines content, secondary provides supplementary
        primary_content = ""
        secondary_content = ""
        for name, content, _ in all_contents.values():
            if name.lower() == primary_name.lower():
                primary_content = content
            else:
                secondary_content = content

        result.primary_content = primary_content or secondary_content

        # Extract supplementary content if both providers have content
        if primary_content and secondary_content:
            supp = extract_supplementary(primary_content, secondary_content)
            if supp:
                result.supplementary = supp

        await log_info(ctx, f"ProviderRouter: parallel run completed with {len(providers)} providers, "
                       f"primary={primary_name}, "
                       f"supplementary={len(result.supplementary)} chars, "
                       f"merged {len(result.all_sources)} sources, "
                       f"errors: {result.errors}",
                       config.debug_enabled)

        return result

    async def _run_fallback(
        self,
        query: str,
        platform: str,
        mode: str,
        providers: list[BaseSearchProvider],
        ctx=None,
    ) -> SearchBatchResult:
        """Try providers in order, fall back on failure."""
        result = SearchBatchResult()
        timeout = float(os.getenv("WEB_SEARCH_GROK_TIMEOUT_SECONDS", "120"))

        for provider in providers:
            name = provider.get_provider_name()
            result.providers_used.append(name)
            try:
                content = await asyncio.wait_for(
                    provider.search(query, platform, mode=mode),
                    timeout=timeout,
                )
                answer_content, sources = split_answer_and_sources(content)
                result.primary_content = answer_content
                result.all_sources = sources
                result.answers.append(ProviderAnswer(name, content))
                return result
            except asyncio.TimeoutError:
                err = f"{name.lower()}_timeout"
                result.errors.append(err)
                result.answers.append(ProviderAnswer(name, "", err))
                continue
            except Exception as e:
                err = f"{name.lower()}_error: {type(e).__name__}: {e}"
                result.errors.append(err)
                result.answers.append(ProviderAnswer(name, "", err))
                continue

        return result

    async def _run_primary(
        self,
        query: str,
        platform: str,
        mode: str,
        providers: list[BaseSearchProvider],
        ctx=None,
    ) -> SearchBatchResult:
        """Use only the primary provider (first configured)."""
        result = SearchBatchResult()
        if not providers:
            result.errors.append("没有可用的搜索 Provider")
            return result

        provider = providers[0]
        name = provider.get_provider_name()
        result.providers_used.append(name)
        timeout = float(os.getenv("WEB_SEARCH_GROK_TIMEOUT_SECONDS", "120"))

        try:
            content = await asyncio.wait_for(
                provider.search(query, platform, mode=mode),
                timeout=timeout,
            )
            answer_content, sources = split_answer_and_sources(content)
            result.primary_content = answer_content
            result.all_sources = sources
            result.answers.append(ProviderAnswer(name, content))
        except asyncio.TimeoutError:
            result.errors.append(f"{name.lower()}_timeout")
            result.answers.append(ProviderAnswer(name, "", f"{name.lower()}_timeout"))
        except Exception as e:
            err = f"{name.lower()}_error: {type(e).__name__}: {e}"
            result.errors.append(err)
            result.answers.append(ProviderAnswer(name, "", err))

        return result


# Global singleton
_router: ProviderRouter | None = None
_router_lock = asyncio.Lock()


async def get_router() -> ProviderRouter:
    """Get or create the global ProviderRouter singleton."""
    global _router
    async with _router_lock:
        if _router is None:
            _router = ProviderRouter()
    return _router

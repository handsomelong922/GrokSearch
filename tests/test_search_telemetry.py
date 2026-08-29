import asyncio

import pytest

from grok_search import entrypoint
from grok_search.providers.router import ProviderRouter
from grok_search.telemetry import get_last_search_timing


class _FakeProvider:
    def __init__(self, name: str, delay: float):
        self._name = name
        self._delay = delay

    def get_provider_name(self):
        return self._name

    async def search(self, query, platform="", mode="balanced"):
        await asyncio.sleep(self._delay)
        return f"{self._name} answer"


@pytest.mark.asyncio
async def test_router_wrapper_records_wall_clock_timing(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER_STRATEGY", "parallel")
    router = ProviderRouter()
    router._providers = [_FakeProvider("Grok", 0.01), _FakeProvider("Gemini", 0.02)]
    router._initialized = True

    result = await router.run_search("timing test")
    timing = get_last_search_timing()

    assert result.router_elapsed_ms > 0
    assert timing["provider_router_ms"] == result.router_elapsed_ms
    assert isinstance(timing["providers_ms"], dict)


@pytest.mark.asyncio
async def test_router_timing_survives_singleflight_style_child_task(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER_STRATEGY", "parallel")
    router = ProviderRouter()
    router._providers = [_FakeProvider("Grok", 0.001)]
    router._initialized = True

    async def fake_single_web_search(**kwargs):
        await asyncio.create_task(router.run_search(kwargs["query"]))
        return {
            "session_id": "child-task",
            "content": kwargs["query"],
            "sources_count": 0,
            "providers_used": ["Grok"],
            "supplementary": "",
        }

    monkeypatch.setattr(entrypoint, "_single_web_search", fake_single_web_search)
    monkeypatch.setattr(entrypoint.server, "_RESULT_CACHE", _FakeCache())

    result = await entrypoint._run_batch(["child context"])
    timing = result["results"][0]["timing"]

    assert timing["provider_router_ms"] > 0


class _FakeCache:
    def __init__(self, cached=None):
        self.cached = cached

    async def get(self, *args, **kwargs):
        return self.cached


@pytest.mark.asyncio
async def test_batch_returns_additive_query_and_batch_timings(monkeypatch):
    async def fake_single_web_search(**kwargs):
        await asyncio.sleep(0.001)
        return {
            "session_id": "session",
            "content": kwargs["query"],
            "sources_count": 0,
            "providers_used": ["Grok", "Gemini"],
            "supplementary": "",
        }

    monkeypatch.setattr(entrypoint, "_single_web_search", fake_single_web_search)
    monkeypatch.setattr(entrypoint.server, "_RESULT_CACHE", _FakeCache())
    monkeypatch.setattr(entrypoint._runtime, "reset_last_search_timing", lambda: None)
    monkeypatch.setattr(
        entrypoint._runtime,
        "get_last_search_timing",
        lambda: {"provider_router_ms": 1.0, "providers_ms": {"Grok": 0.8, "Gemini": 1.0}},
    )

    result = await entrypoint._run_batch(["a", "b"])

    assert result["count"] == 2
    assert result["batch_timing"]["query_count"] == 2
    assert result["batch_timing"]["total_ms"] > 0
    for item in result["results"]:
        assert item["timing"]["total_ms"] > 0
        assert item["timing"]["provider_router_ms"] == 1.0
        assert item["timing"]["providers_ms"]["Gemini"] == 1.0
        assert item["cache_hit"] is False
        assert "content" in item
        assert "sources_count" in item


@pytest.mark.asyncio
async def test_cached_query_is_marked_without_mutating_cached_object(monkeypatch):
    cached = {
        "session_id": "cached",
        "content": "cached answer",
        "sources_count": 0,
    }

    async def fake_single_web_search(**kwargs):
        return cached

    monkeypatch.setattr(entrypoint, "_single_web_search", fake_single_web_search)
    monkeypatch.setattr(entrypoint.server, "_RESULT_CACHE", _FakeCache(cached=cached))
    monkeypatch.setattr(entrypoint._runtime, "reset_last_search_timing", lambda: None)
    monkeypatch.setattr(entrypoint._runtime, "get_last_search_timing", lambda: {})

    result = await entrypoint._run_batch(["cached query"])

    assert result["results"][0]["cache_hit"] is True
    assert "timing" not in cached
    assert "cache_hit" not in cached


@pytest.mark.asyncio
async def test_runtime_diagnostics_are_safe_and_additive(monkeypatch):
    async def fake_base_config():
        return '{"REASONING_EFFORT":"high","SEARCH_PROVIDER_STRATEGY":"parallel"}'

    monkeypatch.setattr(entrypoint._runtime, "_base_get_config_info", fake_base_config)
    monkeypatch.setenv("GIT_SHA", "abc123")
    monkeypatch.setenv("BUILD_VERSION", "26")
    monkeypatch.setenv("DOCKER_TAG", "26")

    raw = await entrypoint.get_config_info()
    assert '"runtime"' in raw
    assert '"git_sha": "abc123"' in raw
    assert '"build_version": "26"' in raw
    assert '"reasoning_effort": "high"' in raw

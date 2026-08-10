import pytest

from grok_search.providers.openai_compatible import OpenAICompatibleSearchProvider


@pytest.mark.asyncio
async def test_search_enables_web_search_and_high_reasoning_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_WEB_SEARCH", raising=False)
    monkeypatch.delenv("REASONING_EFFORT", raising=False)

    provider = OpenAICompatibleSearchProvider(
        api_url="https://example.test/v1",
        api_key="test-key",
        model="grok-test",
    )
    captured = {}

    async def fake_execute(headers, payload, ctx=None):
        captured["payload"] = payload
        return "answer"

    monkeypatch.setattr(provider, "_execute_stream_with_retry", fake_execute)

    assert await provider.search("latest test query") == "answer"
    assert captured["payload"]["tools"] == [{"type": "web_search"}]
    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["payload"]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_search_options_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_WEB_SEARCH", " false ")
    monkeypatch.setenv("REASONING_EFFORT", "none")

    provider = OpenAICompatibleSearchProvider(
        api_url="https://example.test/v1",
        api_key="test-key",
        model="grok-test",
    )
    captured = {}

    async def fake_execute(headers, payload, ctx=None):
        captured["payload"] = payload
        return "answer"

    monkeypatch.setattr(provider, "_execute_stream_with_retry", fake_execute)

    await provider.search("test query")
    assert "tools" not in captured["payload"]
    assert "tool_choice" not in captured["payload"]
    assert "reasoning_effort" not in captured["payload"]


@pytest.mark.asyncio
async def test_fetch_does_not_enable_search_tool(monkeypatch):
    monkeypatch.delenv("ENABLE_WEB_SEARCH", raising=False)
    monkeypatch.delenv("REASONING_EFFORT", raising=False)

    provider = OpenAICompatibleSearchProvider(
        api_url="https://example.test/v1",
        api_key="test-key",
        model="grok-test",
    )
    captured = {}

    async def fake_execute(headers, payload, ctx=None):
        captured["payload"] = payload
        return "document"

    monkeypatch.setattr(provider, "_execute_stream_with_retry", fake_execute)

    assert await provider.fetch("https://example.test/page") == "document"
    assert "tools" not in captured["payload"]
    assert "tool_choice" not in captured["payload"]
    assert "reasoning_effort" not in captured["payload"]

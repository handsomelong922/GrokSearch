import pytest

from grok_search.providers.openai_compatible import OpenAICompatibleSearchProvider


@pytest.mark.asyncio
async def test_search_enables_web_search_and_high_reasoning_by_default(monkeypatch):
    monkeypatch.delenv("OPENAI_API_FORMAT", raising=False)
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
    assert "instructions" in captured["payload"]
    assert "input" in captured["payload"]
    assert captured["payload"]["input"][0]["content"][0]["type"] == "input_text"
    assert captured["payload"]["tools"] == [{"type": "web_search"}]
    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["payload"]["reasoning"] == {"effort": "high"}
    assert "messages" not in captured["payload"]


@pytest.mark.asyncio
async def test_search_options_can_be_disabled(monkeypatch):
    monkeypatch.delenv("OPENAI_API_FORMAT", raising=False)
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
    assert "reasoning" not in captured["payload"]


@pytest.mark.asyncio
async def test_fetch_does_not_enable_search_tool(monkeypatch):
    monkeypatch.delenv("OPENAI_API_FORMAT", raising=False)
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
    assert "instructions" in captured["payload"]
    assert "input" in captured["payload"]
    assert "tools" not in captured["payload"]
    assert "tool_choice" not in captured["payload"]
    assert "reasoning" not in captured["payload"]


@pytest.mark.asyncio
async def test_chat_completions_fallback_preserves_legacy_fields(monkeypatch):
    monkeypatch.setenv("OPENAI_API_FORMAT", "chat_completions")
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

    await provider.search("test query")
    assert captured["payload"]["messages"][0]["role"] == "system"
    assert captured["payload"]["messages"][1]["role"] == "user"
    assert captured["payload"]["tools"] == [{"type": "web_search"}]
    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["payload"]["reasoning_effort"] == "high"
    assert "input" not in captured["payload"]


class _FakeStreamingResponse:
    def __init__(self, lines):
        self.lines = lines

    async def aiter_lines(self):
        for line in self.lines:
            yield line


@pytest.mark.asyncio
async def test_responses_stream_parser_collects_text_and_annotations(monkeypatch):
    monkeypatch.delenv("OPENAI_API_FORMAT", raising=False)
    provider = OpenAICompatibleSearchProvider(
        api_url="https://example.test/v1",
        api_key="test-key",
        model="grok-test",
    )
    response = _FakeStreamingResponse([
        "event: response.output_text.delta",
        'data: {"type":"response.output_text.delta","delta":"Hello "}',
        'data: {"type":"response.output_text.delta","delta":"world"}',
        'data: {"type":"response.output_text.annotation.added","annotation":{"type":"url_citation","url":"https://example.test/source"}}',
        'data: {"type":"response.completed","response":{"output":[]}}',
        "data: [DONE]",
    ])

    result = await provider._parse_responses_streaming_response(response)
    assert result == "Hello world\n\nSources:\n- https://example.test/source"


@pytest.mark.asyncio
async def test_responses_parser_falls_back_to_completed_json(monkeypatch):
    monkeypatch.delenv("OPENAI_API_FORMAT", raising=False)
    provider = OpenAICompatibleSearchProvider(
        api_url="https://example.test/v1",
        api_key="test-key",
        model="grok-test",
    )
    response = _FakeStreamingResponse([
        '{"id":"resp_1","object":"response","output":[{"type":"message","content":[{"type":"output_text","text":"Completed answer"}]}]}',
    ])

    assert await provider._parse_responses_streaming_response(response) == "Completed answer"


@pytest.mark.asyncio
async def test_responses_parser_supports_nested_url_citations_and_event_names(monkeypatch):
    monkeypatch.delenv("OPENAI_API_FORMAT", raising=False)
    provider = OpenAICompatibleSearchProvider(
        api_url="https://example.test/v1",
        api_key="test-key",
        model="grok-test",
    )
    response = _FakeStreamingResponse([
        "event: response.output_text.delta",
        'data: {"delta":"Answer"}',
        "event: response.output_text.annotation.added",
        'data: {"annotation":{"type":"url_citation","url_citation":{"url":"https://example.test/nested"}}}',
        "event: response.completed",
        'data: {"response":{"output":[{"type":"message","content":[{"type":"output_text","text":"Answer","annotations":[{"type":"url_citation","url_citation":{"url":"https://example.test/completed"}}]}]}]}}',
    ])

    result = await provider._parse_responses_streaming_response(response)

    assert result == (
        "Answer\n\nSources:\n"
        "- https://example.test/nested\n"
        "- https://example.test/completed"
    )

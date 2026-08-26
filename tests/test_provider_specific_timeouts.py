import httpx
import pytest

from grok_search.providers import openai_compatible
from grok_search.providers.openai_compatible import OpenAICompatibleSearchProvider


class _Response:
    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        yield "data: [DONE]"


class _StreamContext:
    async def __aenter__(self):
        return _Response()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Client:
    def __init__(self, captured):
        self.captured = captured

    def stream(self, *args, **kwargs):
        self.captured.append(kwargs["timeout"])
        return _StreamContext()


@pytest.mark.asyncio
async def test_grok_and_gemini_use_independent_read_timeouts(monkeypatch):
    monkeypatch.delenv("GROK_HTTP_READ_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("GEMINI_HTTP_READ_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("GROK_RETRY_MAX_ATTEMPTS", "0")

    captured = []

    async def fake_get_shared_client(timeout=None):
        return _Client(captured)

    monkeypatch.setattr(openai_compatible, "_get_shared_client", fake_get_shared_client)

    grok = OpenAICompatibleSearchProvider(
        api_url="https://grok.example/v1",
        api_key="test-key",
        model="grok-test",
        provider_name="Grok",
    )
    gemini = OpenAICompatibleSearchProvider(
        api_url="https://gemini.example/v1",
        api_key="test-key",
        model="gemini-test",
        provider_name="Gemini",
    )

    await grok._execute_stream_with_retry({}, {"stream": True})
    await gemini._execute_stream_with_retry({}, {"stream": True})

    assert captured[0].read == 10.0
    assert captured[1].read == 15.0


def test_read_timeout_remains_non_retryable():
    """A stalled open stream should not multiply its timeout through retries."""
    exc = httpx.ReadTimeout("stream stalled")

    assert openai_compatible._is_retryable_exception(exc) is False

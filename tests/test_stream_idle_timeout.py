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
    def stream(self, *args, **kwargs):
        return _StreamContext()


@pytest.mark.asyncio
async def test_stream_read_timeout_defaults_to_10_seconds(monkeypatch):
    """Silent streaming reads should be capped at 10s by default."""
    monkeypatch.delenv("GROK_HTTP_READ_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("GROK_RETRY_MAX_ATTEMPTS", "0")
    captured = {}

    async def fake_get_shared_client(timeout):
        captured["timeout"] = timeout
        return _Client()

    monkeypatch.setattr(openai_compatible, "_get_shared_client", fake_get_shared_client)

    provider = OpenAICompatibleSearchProvider(
        api_url="https://example.test/v1",
        api_key="test-key",
        model="grok-test",
    )

    await provider._execute_stream_with_retry({}, {"stream": True})

    assert captured["timeout"].read == 10.0


def test_stream_read_timeout_is_not_retried():
    """A stalled open stream must not multiply its timeout through retries."""
    exc = httpx.ReadTimeout("stream stalled")

    assert openai_compatible._is_retryable_exception(exc) is False

import asyncio

import pytest

from grok_search.providers.openai_compatible import OpenAICompatibleSearchProvider


class _StalledStreamingResponse:
    async def aiter_lines(self):
        await asyncio.sleep(60)
        yield "data: never"


@pytest.mark.asyncio
async def test_responses_stream_stall_raises_idle_timeout_before_outer_deadline(monkeypatch):
    """A silent HTTP 200 stream should fail on idle timeout, not the 80s total timeout."""
    monkeypatch.delenv("OPENAI_API_FORMAT", raising=False)
    monkeypatch.setenv("GROK_STREAM_IDLE_TIMEOUT_SECONDS", "0.01")

    provider = OpenAICompatibleSearchProvider(
        api_url="https://example.test/v1",
        api_key="test-key",
        model="grok-test",
    )

    with pytest.raises(Exception) as excinfo:
        await asyncio.wait_for(
            provider._parse_responses_streaming_response(_StalledStreamingResponse()),
            timeout=0.05,
        )

    assert type(excinfo.value).__name__ == "StreamIdleTimeoutError"

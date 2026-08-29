import pytest

from grok_search import entrypoint


class _Response:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


class _Client:
    def __init__(self, response: _Response):
        self.response = response
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_direct_fetch_reads_github_api_without_tavily(monkeypatch):
    client = _Client(_Response('{"name":"main"}'))

    async def fake_client():
        return client

    monkeypatch.setattr(entrypoint.server, "_get_supplemental_client", fake_client)

    result = await entrypoint._call_direct_fetch(
        "https://api.github.com/repos/handsomelong922/GrokSearch/branches/main"
    )

    assert result == '{"name":"main"}'
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_direct_fetch_reads_raw_github_content(monkeypatch):
    client = _Client(_Response("print('ok')\n"))

    async def fake_client():
        return client

    monkeypatch.setattr(entrypoint.server, "_get_supplemental_client", fake_client)

    result = await entrypoint._call_direct_fetch(
        "https://raw.githubusercontent.com/handsomelong922/GrokSearch/main/README.md"
    )

    assert result == "print('ok')\n"


@pytest.mark.asyncio
async def test_direct_fetch_skips_non_github_hosts(monkeypatch):
    async def fail_if_called():
        raise AssertionError("HTTP client should not be created for unsupported hosts")

    monkeypatch.setattr(entrypoint.server, "_get_supplemental_client", fail_if_called)

    assert await entrypoint._call_direct_fetch("https://example.com/page") is None


@pytest.mark.asyncio
async def test_web_fetch_prefers_direct_github_path(monkeypatch):
    async def direct(url):
        return '{"sha":"abc123"}'

    async def base_fetch(url, ctx=None):
        raise AssertionError("Tavily/Firecrawl base fetch should not run when direct GitHub fetch succeeds")

    monkeypatch.setattr(entrypoint, "_call_direct_fetch", direct)
    monkeypatch.setattr(entrypoint, "_base_web_fetch", base_fetch)

    result = await entrypoint.web_fetch(
        "https://api.github.com/repos/handsomelong922/GrokSearch/branches/main"
    )

    assert result == '{"sha":"abc123"}'


@pytest.mark.asyncio
async def test_web_fetch_keeps_existing_path_for_normal_pages(monkeypatch):
    async def base_fetch(url, ctx=None):
        return "normal page"

    monkeypatch.setattr(entrypoint, "_base_web_fetch", base_fetch)

    result = await entrypoint.web_fetch("https://example.com/page")

    assert result == "normal page"

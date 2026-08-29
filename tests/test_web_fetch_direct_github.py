import pytest

from grok_search import server


class _Response:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


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

    monkeypatch.setattr(server, "_get_supplemental_client", fake_client)

    result = await server._call_direct_fetch(
        "https://api.github.com/repos/handsomelong922/GrokSearch/branches/main"
    )

    assert result == '{"name":"main"}'
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_direct_fetch_reads_raw_github_content(monkeypatch):
    client = _Client(_Response("print('ok')\n"))

    async def fake_client():
        return client

    monkeypatch.setattr(server, "_get_supplemental_client", fake_client)

    result = await server._call_direct_fetch(
        "https://raw.githubusercontent.com/handsomelong922/GrokSearch/main/README.md"
    )

    assert result == "print('ok')\n"


@pytest.mark.asyncio
async def test_direct_fetch_skips_non_github_hosts(monkeypatch):
    async def fail_if_called():
        raise AssertionError("HTTP client should not be created for unsupported hosts")

    monkeypatch.setattr(server, "_get_supplemental_client", fail_if_called)

    assert await server._call_direct_fetch("https://example.com/page") is None


@pytest.mark.asyncio
async def test_web_fetch_prefers_direct_github_path(monkeypatch):
    async def direct(url):
        return '{"sha":"abc123"}'

    async def tavily(url):
        raise AssertionError("Tavily should not run when direct GitHub fetch succeeds")

    async def firecrawl(url, ctx=None):
        raise AssertionError("Firecrawl should not run when direct GitHub fetch succeeds")

    monkeypatch.setattr(server, "_call_direct_fetch", direct)
    monkeypatch.setattr(server, "_call_tavily_extract", tavily)
    monkeypatch.setattr(server, "_call_firecrawl_scrape", firecrawl)

    result = await server.web_fetch(
        "https://api.github.com/repos/handsomelong922/GrokSearch/branches/main"
    )

    assert result == '{"sha":"abc123"}'

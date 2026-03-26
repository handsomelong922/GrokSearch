import pytest

from grok_search.config import Config
from grok_search import server


def test_tavily_api_keys_parse_csv(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", " key-a ,key-b,  , key-c ")
    cfg = Config()
    cfg._tavily_key_index = 0

    assert cfg.tavily_api_keys == ["key-a", "key-b", "key-c"]


def test_tavily_api_keys_rotation_order(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "key-a,key-b")
    cfg = Config()
    cfg._tavily_key_index = 0

    assert cfg.get_tavily_api_keys_in_rotation_order() == ["key-a", "key-b"]
    assert cfg.get_tavily_api_keys_in_rotation_order() == ["key-b", "key-a"]
    assert cfg.get_tavily_api_keys_in_rotation_order() == ["key-a", "key-b"]


@pytest.mark.asyncio
async def test_tavily_search_tries_next_key_when_first_fails(monkeypatch):
    import httpx

    monkeypatch.setenv("TAVILY_API_KEY", "key-a,key-b")
    server.config._tavily_key_index = 0

    used_auth_headers: list[str] = []

    class MockResponse:
        def __init__(self, should_fail: bool):
            self._should_fail = should_fail

        def raise_for_status(self):
            if self._should_fail:
                raise RuntimeError("bad key")

        def json(self):
            return {
                "results": [{
                    "title": "t",
                    "url": "https://example.com",
                    "content": "c",
                    "score": 1,
                }]
            }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, headers=None, json=None):
            used_auth_headers.append(headers.get("Authorization", ""))
            should_fail = headers.get("Authorization") == "Bearer key-a"
            return MockResponse(should_fail=should_fail)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    results = await server._call_tavily_search("hello", 1)

    assert results == [{
        "title": "t",
        "url": "https://example.com",
        "content": "c",
        "score": 1,
    }]
    assert used_auth_headers == ["Bearer key-a", "Bearer key-b"]

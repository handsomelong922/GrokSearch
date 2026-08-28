import json
import os

import pytest

from grok_search import entrypoint


class FakeConfig:
    def __init__(self):
        self.data = {"model": "grok-chat-fast"}
        self.grok_model = "grok-chat-fast"
        self.gemini_model = "gemini-3.6-flash"
        self.grok_api_url = "https://grok.test/v1"
        self.grok_api_key = "grok-key"
        self.gemini_api_url = "https://gemini.test/v1"
        self.gemini_api_key = "gemini-key"
        self.config_file = "/tmp/config.json"
        self._cached_model = self.grok_model

    def _load_config_file(self):
        return dict(self.data)

    def _save_config_file(self, value):
        self.data = dict(value)

    def _apply_model_suffix(self, model):
        return model


@pytest.mark.asyncio
async def test_switch_model_can_target_gemini_without_changing_grok(monkeypatch):
    cfg = FakeConfig()
    resets = 0

    async def fake_models(api_url, api_key):
        assert (api_url, api_key) == (cfg.gemini_api_url, cfg.gemini_api_key)
        return ["gemini-3.6-flash", "gemini-3.7-flash"]

    async def fake_reset():
        nonlocal resets
        resets += 1

    monkeypatch.setattr(entrypoint.server, "config", cfg)
    monkeypatch.setattr(entrypoint.server, "_get_available_models_cached", fake_models)
    monkeypatch.setattr(entrypoint, "_reset_provider_router", fake_reset)
    monkeypatch.setenv("GROK_MODEL", "grok-chat-fast")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")

    result = json.loads(
        await entrypoint.switch_model(
            model="gemini-3.7-flash",
            provider="gemini",
        )
    )

    assert result["status"] == "✅ 成功"
    assert result["provider"] == "Gemini"
    assert result["previous_model"] == "gemini-3.6-flash"
    assert result["current_model"] == "gemini-3.7-flash"
    assert cfg.data["GEMINI_MODEL"] == "gemini-3.7-flash"
    assert cfg.data["model"] == "grok-chat-fast"
    assert os.environ["GROK_MODEL"] == "grok-chat-fast"
    assert os.environ["GEMINI_MODEL"] == "gemini-3.7-flash"
    assert resets == 1


@pytest.mark.asyncio
async def test_switch_model_legacy_call_still_targets_grok(monkeypatch):
    cfg = FakeConfig()

    async def fake_models(api_url, api_key):
        assert (api_url, api_key) == (cfg.grok_api_url, cfg.grok_api_key)
        return ["grok-chat-fast", "grok-4.6"]

    async def fake_reset():
        return None

    monkeypatch.setattr(entrypoint.server, "config", cfg)
    monkeypatch.setattr(entrypoint.server, "_get_available_models_cached", fake_models)
    monkeypatch.setattr(entrypoint, "_reset_provider_router", fake_reset)
    monkeypatch.setenv("GROK_MODEL", "grok-chat-fast")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")

    result = json.loads(await entrypoint.switch_model(model="grok-4.6"))

    assert result["provider"] == "Grok"
    assert cfg.data["GROK_MODEL"] == "grok-4.6"
    assert cfg.data["model"] == "grok-4.6"
    assert "GEMINI_MODEL" not in cfg.data
    assert os.environ["GEMINI_MODEL"] == "gemini-3.6-flash"


@pytest.mark.asyncio
async def test_switch_model_rejects_unknown_provider(monkeypatch):
    cfg = FakeConfig()
    monkeypatch.setattr(entrypoint.server, "config", cfg)

    result = json.loads(
        await entrypoint.switch_model(model="anything", provider="other")
    )

    assert result["status"] == "❌ 失败"
    assert result["error"] == "unsupported_provider"
    assert cfg.data == {"model": "grok-chat-fast"}


@pytest.mark.asyncio
async def test_switch_model_rejects_model_missing_from_provider_catalog(monkeypatch):
    cfg = FakeConfig()

    async def fake_models(api_url, api_key):
        return ["gemini-3.6-flash", "gemini-3.7-flash"]

    monkeypatch.setattr(entrypoint.server, "config", cfg)
    monkeypatch.setattr(entrypoint.server, "_get_available_models_cached", fake_models)

    result = json.loads(
        await entrypoint.switch_model(
            model="gemini-does-not-exist",
            provider="gemini",
        )
    )

    assert result["status"] == "❌ 失败"
    assert result["error"] == "model_not_available"
    assert "GEMINI_MODEL" not in cfg.data


def test_apply_persisted_provider_model_overrides_wins_over_deployment_env(monkeypatch):
    cfg = FakeConfig()
    cfg.data = {
        "model": "grok-old",
        "GROK_MODEL": "grok-persisted",
        "GEMINI_MODEL": "gemini-3.7-flash",
    }
    monkeypatch.setattr(entrypoint.server, "config", cfg)
    monkeypatch.setenv("GROK_MODEL", "grok-deployment")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")

    entrypoint._apply_persisted_provider_model_overrides()

    assert os.environ["GROK_MODEL"] == "grok-persisted"
    assert os.environ["GEMINI_MODEL"] == "gemini-3.7-flash"
    assert cfg._cached_model is None

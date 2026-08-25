from grok_search.config import config


def test_reasoning_effort_defaults_to_low_for_lower_search_latency(monkeypatch):
    monkeypatch.delenv("REASONING_EFFORT", raising=False)

    assert config.reasoning_effort == "low"


def test_reasoning_effort_env_override_is_preserved(monkeypatch):
    monkeypatch.setenv("REASONING_EFFORT", "high")

    assert config.reasoning_effort == "high"

from __future__ import annotations

import pytest

from slay2agent.config import (
    DEFAULT_LLM_MODEL,
    DEFAULT_STS2MCP_BASE_URL,
    Config,
    GameConfig,
    LLMConfig,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_TIMEOUT",
        "LLM_BASE_URL",
        "STS2MCP_BASE_URL",
        "STS2MCP_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_llm_config_defaults_when_env_empty() -> None:
    cfg = LLMConfig.from_env()
    assert cfg.model == DEFAULT_LLM_MODEL
    assert cfg.api_key is None
    assert cfg.timeout == 120.0


def test_llm_config_picks_up_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-or-test")
    monkeypatch.setenv("LLM_MODEL", "anthropic/claude-sonnet-4")
    monkeypatch.setenv("LLM_TIMEOUT", "45")
    cfg = LLMConfig.from_env()
    assert cfg.api_key == "sk-or-test"
    assert cfg.model == "anthropic/claude-sonnet-4"
    assert cfg.timeout == 45.0


def test_llm_config_require_api_key_raises_when_missing() -> None:
    cfg = LLMConfig.from_env()
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        cfg.require_api_key()


def test_game_config_defaults_when_env_empty() -> None:
    cfg = GameConfig.from_env()
    assert cfg.base_url == DEFAULT_STS2MCP_BASE_URL
    assert cfg.timeout == 30.0


def test_game_config_picks_up_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STS2MCP_BASE_URL", "http://127.0.0.1:9001")
    monkeypatch.setenv("STS2MCP_TIMEOUT", "10")
    cfg = GameConfig.from_env()
    assert cfg.base_url == "http://127.0.0.1:9001"
    assert cfg.timeout == 10.0


def test_config_load_skips_dotenv_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-or-test")
    monkeypatch.setenv("STS2MCP_BASE_URL", "http://127.0.0.1:8080")
    cfg = Config.load(dotenv=False)
    assert cfg.llm.api_key == "sk-or-test"
    assert cfg.game.base_url == "http://127.0.0.1:8080"

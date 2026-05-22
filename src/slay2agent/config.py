"""Runtime configuration for slay2agent.

Loads cloud LLM credentials and STS2MCP REST coordinates from environment.
No GPU, no local model paths — see F-001 in docs/feature-requirements.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

LLMProvider = Literal["openai_compat", "openrouter", "openai", "deepseek"]

DEFAULT_LLM_PROVIDER: LLMProvider = "openrouter"
DEFAULT_LLM_MODEL = "openai/gpt-4.1-mini"
DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENAI_LLM_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_DEEPSEEK_LLM_MODEL = "deepseek-v4-pro"
DEFAULT_DEEPSEEK_LLM_BASE_URL = "https://api.deepseek.com"

_PROVIDER_DEFAULTS: dict[LLMProvider, tuple[str, str]] = {
    "openai_compat": (DEFAULT_LLM_MODEL, DEFAULT_LLM_BASE_URL),
    "openrouter": (DEFAULT_LLM_MODEL, DEFAULT_LLM_BASE_URL),
    "openai": (DEFAULT_OPENAI_LLM_MODEL, DEFAULT_OPENAI_LLM_BASE_URL),
    "deepseek": (DEFAULT_DEEPSEEK_LLM_MODEL, DEFAULT_DEEPSEEK_LLM_BASE_URL),
}


def _llm_provider_from_env(raw: str | None) -> LLMProvider:
    provider = (raw or DEFAULT_LLM_PROVIDER).strip().lower()
    if provider not in _PROVIDER_DEFAULTS:
        valid = ", ".join(_PROVIDER_DEFAULTS)
        raise RuntimeError(f"Unsupported LLM_PROVIDER={provider!r}. Expected one of: {valid}.")
    return provider  # type: ignore[return-value]


@dataclass(frozen=True)
class LLMConfig:
    provider: LLMProvider = DEFAULT_LLM_PROVIDER
    model: str = ""
    api_key: str | None = None
    base_url: str = ""
    timeout: float = 120.0
    thinking_budget: int | None = None
    """Token budget for the **main** agent's reasoning chain.

    Maps to ``extra_body["thinking_budget"]``.
    Set via ``LLM_THINKING_BUDGET`` env var.
    """
    subagent_thinking_budget: int | None = None
    """Token budget for **sub-agent** (skill_creator / oracle_updater) reasoning.

    Falls back to ``thinking_budget`` when ``None``.
    Set via ``LLM_SUBAGENT_THINKING_BUDGET`` env var.
    """
    enable_thinking: bool | None = None
    """Explicit on/off for reasoning mode (applied to all agents).

    Maps to ``extra_body["enable_thinking"]``.
    Set via ``LLM_ENABLE_THINKING`` env var (``"true"`` / ``"false"``).
    ``None`` = not sent (provider default).
    """

    def __post_init__(self) -> None:
        provider = _llm_provider_from_env(self.provider)
        default_model, default_base_url = _PROVIDER_DEFAULTS[provider]
        object.__setattr__(self, "provider", provider)
        if not self.model:
            object.__setattr__(self, "model", default_model)
        if not self.base_url:
            object.__setattr__(self, "base_url", default_base_url)

    @classmethod
    def from_env(cls) -> "LLMConfig":
        provider = _llm_provider_from_env(os.getenv("LLM_PROVIDER"))
        default_model, default_base_url = _PROVIDER_DEFAULTS[provider]
        budget_raw = os.getenv("LLM_THINKING_BUDGET")
        subagent_budget_raw = os.getenv("LLM_SUBAGENT_THINKING_BUDGET")
        thinking_raw = os.getenv("LLM_ENABLE_THINKING")
        return cls(
            provider=provider,
            model=os.getenv("LLM_MODEL") or default_model,
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL") or default_base_url,
            timeout=float(os.getenv("LLM_TIMEOUT", "120")),
            thinking_budget=int(budget_raw) if budget_raw else None,
            subagent_thinking_budget=int(subagent_budget_raw) if subagent_budget_raw else None,
            enable_thinking=(
                thinking_raw.lower() == "true" if thinking_raw is not None else None
            ),
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "LLM_API_KEY is not set. Put it in .env or export it."
            )
        return self.api_key

    @property
    def extra_body(self) -> dict[str, Any] | None:
        """extra_body for the main agent."""
        if self.provider == "deepseek":
            return self._deepseek_extra_body()
        d: dict[str, Any] = {}
        if self.enable_thinking is not None:
            d["enable_thinking"] = self.enable_thinking
        if self.thinking_budget is not None:
            d["thinking_budget"] = self.thinking_budget
        return d or None

    @property
    def subagent_extra_body(self) -> dict[str, Any] | None:
        """extra_body for skill_creator / oracle_updater.

        Uses ``subagent_thinking_budget`` when set, otherwise falls back to
        ``thinking_budget``.  ``enable_thinking`` applies to both agents.
        """
        if self.provider == "deepseek":
            return self._deepseek_extra_body()
        budget = (
            self.subagent_thinking_budget
            if self.subagent_thinking_budget is not None
            else self.thinking_budget
        )
        d: dict[str, Any] = {}
        if self.enable_thinking is not None:
            d["enable_thinking"] = self.enable_thinking
        if budget is not None:
            d["thinking_budget"] = budget
        return d or None

    def _deepseek_extra_body(self) -> dict[str, Any] | None:
        if self.enable_thinking is None:
            return None
        thinking_type = "enabled" if self.enable_thinking else "disabled"
        return {"thinking": {"type": thinking_type}}


DEFAULT_STS2MCP_BASE_URL = "http://127.0.0.1:15526"


@dataclass(frozen=True)
class GameConfig:
    """STS2MCP REST endpoint config.

    Default port 15526 matches the upstream mod
    (https://github.com/Gennadiyev/STS2MCP).
    """

    base_url: str = DEFAULT_STS2MCP_BASE_URL
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "GameConfig":
        return cls(
            base_url=os.getenv("STS2MCP_BASE_URL", DEFAULT_STS2MCP_BASE_URL),
            timeout=float(os.getenv("STS2MCP_TIMEOUT", "30")),
        )


DEFAULT_AGENT_STATE_DIR = "agent_state"


@dataclass(frozen=True)
class MemoryConfig:
    """Paths for the L1/L2 memory layer (skill library + oracle).

    L0 Compaction config (F-012):
      l0_compact_enabled    — set ``L0_COMPACT_ENABLED=false`` to disable.
      l0_compact_threshold  — trigger compaction when len(l0) exceeds this value.
      l0_compact_keep       — keep the most-recent K messages verbatim;
                              everything older is replaced by a summary.
    """

    agent_state_dir: Path = Path(DEFAULT_AGENT_STATE_DIR)
    oracle_max_tokens: int = 4000
    l0_compact_enabled: bool = True
    l0_compact_threshold: int = 30
    l0_compact_keep: int = 6

    @classmethod
    def from_env(cls) -> "MemoryConfig":
        compact_raw = os.getenv("L0_COMPACT_ENABLED")
        return cls(
            agent_state_dir=Path(
                os.getenv("AGENT_STATE_DIR", DEFAULT_AGENT_STATE_DIR)
            ),
            oracle_max_tokens=int(os.getenv("ORACLE_MAX_TOKENS", "4000")),
            l0_compact_enabled=(
                compact_raw.lower() != "false" if compact_raw is not None else True
            ),
            l0_compact_threshold=int(os.getenv("L0_COMPACT_THRESHOLD", "30")),
            l0_compact_keep=int(os.getenv("L0_COMPACT_KEEP", "6")),
        )

    @property
    def skills_dir(self) -> Path:
        return self.agent_state_dir / "skills"

    @property
    def skill_cache_path(self) -> Path:
        return self.agent_state_dir / "skill_cache.json"

    @property
    def oracle_path(self) -> Path:
        return self.agent_state_dir / "oracle.md"

    @property
    def issues_path(self) -> Path:
        return self.agent_state_dir / "issues.jsonl"


@dataclass(frozen=True)
class Config:
    llm: LLMConfig
    game: GameConfig
    memory: MemoryConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Allow memory to be omitted in existing call sites; default it here.
        if self.memory is None:
            object.__setattr__(self, "memory", MemoryConfig())

    @classmethod
    def load(cls, *, dotenv: bool = True) -> "Config":
        if dotenv:
            load_dotenv()
        return cls(
            llm=LLMConfig.from_env(),
            game=GameConfig.from_env(),
            memory=MemoryConfig.from_env(),
        )

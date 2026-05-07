"""Runtime configuration for slay2agent.

Loads cloud LLM credentials and STS2MCP REST coordinates from environment.
No GPU, no local model paths — see F-001 in docs/feature-requirements.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_LLM_MODEL = "openai/gpt-4o-mini"


@dataclass(frozen=True)
class LLMConfig:
    model: str = DEFAULT_LLM_MODEL
    api_key: str | None = None
    timeout: float = 120.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            model=os.getenv("OPENROUTER_MODEL", DEFAULT_LLM_MODEL),
            api_key=os.getenv("OPENROUTER_API_KEY"),
            timeout=float(os.getenv("OPENROUTER_TIMEOUT", "120")),
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Put it in .env or export it."
            )
        return self.api_key


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


@dataclass(frozen=True)
class Config:
    llm: LLMConfig
    game: GameConfig

    @classmethod
    def load(cls, *, dotenv: bool = True) -> "Config":
        if dotenv:
            load_dotenv()
        return cls(llm=LLMConfig.from_env(), game=GameConfig.from_env())

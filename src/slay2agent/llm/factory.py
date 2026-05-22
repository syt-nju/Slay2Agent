"""Factory for constructing configured LLM adapters."""

from __future__ import annotations

from slay2agent.config import LLMConfig
from slay2agent.llm.deepseek import DeepSeekAdapter
from slay2agent.llm.openai_compat import OpenAICompatibleAdapter
from slay2agent.llm.protocol import LLMAdapter


def build_llm_adapter(cfg: LLMConfig) -> LLMAdapter:
    api_key = cfg.require_api_key()
    if cfg.provider == "deepseek":
        return DeepSeekAdapter(
            model=cfg.model,
            api_key=api_key,
            base_url=cfg.base_url,
            timeout=cfg.timeout,
        )
    return OpenAICompatibleAdapter(
        model=cfg.model,
        api_key=api_key,
        base_url=cfg.base_url,
        timeout=cfg.timeout,
    )

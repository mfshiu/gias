# src/llm/providers/factory.py
from __future__ import annotations

from typing import Optional

from ..config import LLMRuntimeConfig
from ..types import ProviderClient


def build_provider_client(cfg: LLMRuntimeConfig) -> tuple[ProviderClient, Optional[str], Optional[str]]:
    """
    回傳：(provider_client, openai_api_key, embed_model)
    - openai_api_key/embed_model 只在 openai 時有用
    """
    provider = cfg.provider

    if provider == "openai":
        from .openai_provider import OpenAIProvider

        if not cfg.openai_api_key:
            raise RuntimeError("llm.openai.api_key is required in gias.toml for OpenAI provider.")
        client = OpenAIProvider(api_key=cfg.openai_api_key)
        return client, cfg.openai_api_key, (cfg.openai_embed_model or "text-embedding-3-small")

    if provider == "ollama":
        from .ollama_provider import OllamaProvider

        client = OllamaProvider(
            base_url=cfg.ollama_base_url or "http://localhost:11434",
            model=cfg.ollama_model or "llama3",
        )
        return client, None, None

    if provider == "mock":
        from .mock_provider import MockProvider

        return MockProvider(), None, None

    raise RuntimeError(f"Unknown llm.provider in gias.toml: {provider!r}")

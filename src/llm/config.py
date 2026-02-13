# src/llm/config.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class LLMRuntimeConfig:
    provider: str
    strict_json: bool = True
    timeout_sec: float = 60.0
    max_retries: int = 2
    retry_backoff: float = 1.5
    retry_jitter: float = 0.1

    # openai
    openai_api_key: Optional[str] = None
    openai_embed_model: Optional[str] = None

    # ollama
    ollama_base_url: Optional[str] = None
    ollama_model: Optional[str] = None


def load_llm_runtime_config(agent_config: Dict[str, Any]) -> LLMRuntimeConfig:
    llm_cfg = agent_config.get("llm")
    if not isinstance(llm_cfg, dict):
        raise RuntimeError("Missing [llm] config in gias.toml (agent_config['llm']).")

    provider = str(llm_cfg.get("provider", "openai")).lower()

    strict_json = bool(llm_cfg.get("strict_json", True))
    timeout = float(llm_cfg.get("timeout_sec", 60))
    max_retries = int(llm_cfg.get("max_retries", 2))
    backoff = float(llm_cfg.get("retry_backoff", 1.5))
    jitter = float(llm_cfg.get("retry_jitter", 0.1))

    openai_cfg = llm_cfg.get("openai") or {}
    ollama_cfg = llm_cfg.get("ollama") or {}

    openai_api_key = openai_cfg.get("api_key")
    openai_embed_model = openai_cfg.get("embed_model", "text-embedding-3-small") if openai_cfg else None

    ollama_base_url = ollama_cfg.get("base_url", "http://localhost:11434") if ollama_cfg else None
    ollama_model = ollama_cfg.get("model", "llama3") if ollama_cfg else None

    return LLMRuntimeConfig(
        provider=provider,
        strict_json=strict_json,
        timeout_sec=timeout,
        max_retries=max_retries,
        retry_backoff=backoff,
        retry_jitter=jitter,
        openai_api_key=openai_api_key,
        openai_embed_model=openai_embed_model,
        ollama_base_url=ollama_base_url,
        ollama_model=ollama_model,
    )

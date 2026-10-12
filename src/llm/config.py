# src/llm/config.py
from __future__ import annotations

import os
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
    # embedding 快取筆數（process 內共用的 LRU）；0 表示不快取
    embedding_cache_size: int = 2048

    # openai
    openai_api_key: Optional[str] = None
    openai_embed_model: Optional[str] = None
    openai_model: str = "gpt-4.1-mini"          # 聊天模型（[llm.openai] model）
    openai_base_url: Optional[str] = None       # 相容 OpenAI API 的其他端點（[llm.openai] base_url）
    openai_use_responses: Optional[bool] = None  # True 走 Responses API；None 依 GIAS_OPENAI_USE_RESPONSES

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
    embedding_cache_size = int(llm_cfg.get("embedding_cache_size", 2048))

    openai_cfg = llm_cfg.get("openai") or {}
    ollama_cfg = llm_cfg.get("ollama") or {}

    # 設定檔沒有金鑰時讀 OPENAI_API_KEY 環境變數（S-03：金鑰不必寫在 gias.toml）
    openai_api_key = openai_cfg.get("api_key") or os.environ.get("OPENAI_API_KEY") or None
    openai_embed_model = openai_cfg.get("embed_model", "text-embedding-3-small") if openai_cfg else None
    openai_model = str(openai_cfg.get("model") or "gpt-4.1-mini")
    openai_base_url = openai_cfg.get("base_url") or None
    use_responses = openai_cfg.get("use_responses")
    if isinstance(use_responses, str):
        use_responses = use_responses.strip().lower() in ("1", "true", "yes", "on")
    openai_use_responses = None if use_responses is None else bool(use_responses)

    ollama_base_url = ollama_cfg.get("base_url", "http://localhost:11434") if ollama_cfg else None
    ollama_model = ollama_cfg.get("model", "llama3") if ollama_cfg else None

    return LLMRuntimeConfig(
        provider=provider,
        strict_json=strict_json,
        timeout_sec=timeout,
        max_retries=max_retries,
        retry_backoff=backoff,
        retry_jitter=jitter,
        embedding_cache_size=embedding_cache_size,
        openai_api_key=openai_api_key,
        openai_embed_model=openai_embed_model,
        openai_model=openai_model,
        openai_base_url=openai_base_url,
        openai_use_responses=openai_use_responses,
        ollama_base_url=ollama_base_url,
        ollama_model=ollama_model,
    )

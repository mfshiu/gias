# src/llm/client.py
# LLMClient：統一呼叫入口（chat/json/embed）
# ✅ 本版完全使用 gias.toml（agent_config），不再讀 .env

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence
from typing import cast
from .types import EmbeddingProviderClient, LLMResponse, ProviderClient, SchemaType
from .config import load_llm_runtime_config
from .providers.factory import build_provider_client
from .retry import call_with_retry, is_retriable_exception
from .normalize import normalize_response
from .json_utils import parse_json, validate_schema
from .embedding import normalize_embedding
from .errors import (
    LLMError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMEmbeddingNotSupportedError,
)


class LLMClient:
    def __init__(
        self,
        provider_client: ProviderClient,
        *,
        provider_name: str,
        default_timeout: Optional[float],
        default_max_retries: int,
        default_retry_backoff: float,
        default_retry_jitter: float,
        strict_json: bool,
        default_embed_model: Optional[str],
        openai_api_key: Optional[str],
    ):
        self.provider_client = provider_client
        self.provider_name = (provider_name or "openai").lower()

        self.default_timeout = default_timeout
        self.default_max_retries = default_max_retries
        self.default_retry_backoff = default_retry_backoff
        self.default_retry_jitter = default_retry_jitter
        self.strict_json = strict_json

        self.default_embed_model = default_embed_model
        self._openai_api_key = openai_api_key
        self._openai_client = None

    @classmethod
    def from_config(cls, agent_config: dict) -> "LLMClient":
        cfg = load_llm_runtime_config(agent_config)
        provider_client, openai_api_key, embed_model = build_provider_client(cfg)

        return cls(
            provider_client=provider_client,
            provider_name=cfg.provider,
            default_timeout=cfg.timeout_sec,
            default_max_retries=cfg.max_retries,
            default_retry_backoff=cfg.retry_backoff,
            default_retry_jitter=cfg.retry_jitter,
            strict_json=cfg.strict_json,
            default_embed_model=embed_model,
            openai_api_key=openai_api_key,
        )

    # ---- Public API ----

    def chat(self, messages: Sequence[Dict[str, Any]], **kwargs) -> LLMResponse:
        raw = self._call_chat(messages, **kwargs)
        return normalize_response(raw)

    def json(self, messages: Sequence[Dict[str, Any]], schema: SchemaType = None, **kwargs) -> Any:
        raw = self._call_chat(messages, **kwargs)
        resp = normalize_response(raw)
        obj = parse_json(resp.content, strict_json=self.strict_json)
        return validate_schema(obj, schema)

    def embed_text(self, text: str, **kwargs) -> List[float]:
        raw = self._call_embed(text, **kwargs)
        return normalize_embedding(raw)

    # ---- Internal: chat ----

    def _call_chat(self, messages: Sequence[Dict[str, Any]], **kwargs) -> Any:
        timeout = kwargs.pop("timeout", self.default_timeout)
        max_retries = kwargs.pop("max_retries", self.default_max_retries)
        backoff = kwargs.pop("retry_backoff", self.default_retry_backoff)
        jitter = kwargs.pop("retry_jitter", self.default_retry_jitter)

        def _do():
            call_kwargs = dict(kwargs)
            if timeout is not None and "timeout" not in call_kwargs:
                call_kwargs["timeout"] = timeout
            return self.provider_client.chat(messages, **call_kwargs)

        return call_with_retry(
            _do,
            max_retries=max_retries,
            backoff=backoff,
            jitter=jitter,
            is_retriable=is_retriable_exception,
            wrap_exception=self._wrap_provider_exception,
        )

    # ---- Internal: embedding ----

    def _call_embed(self, text: str, **kwargs) -> Any:
        timeout = kwargs.pop("timeout", self.default_timeout)
        max_retries = kwargs.pop("max_retries", self.default_max_retries)
        backoff = kwargs.pop("retry_backoff", self.default_retry_backoff)
        jitter = kwargs.pop("retry_jitter", self.default_retry_jitter)

        def _do():
            call_kwargs = dict(kwargs)
            if timeout is not None and "timeout" not in call_kwargs:
                call_kwargs["timeout"] = timeout

            provider_embed = getattr(self.provider_client, "embed_text", None)
            if callable(provider_embed):
                return cast(EmbeddingProviderClient, self.provider_client).embed_text(text, **call_kwargs)

            if self.provider_name == "openai":
                return self._openai_embed(text, **call_kwargs)

            raise LLMEmbeddingNotSupportedError(f"Provider '{self.provider_name}' does not support embeddings.")

        return call_with_retry(
            _do,
            max_retries=max_retries,
            backoff=backoff,
            jitter=jitter,
            is_retriable=is_retriable_exception,
            wrap_exception=self._wrap_provider_exception,
        )

    def _openai_embed(self, text: str, **kwargs) -> Any:
        if not self._openai_api_key:
            raise RuntimeError("OpenAI embeddings require llm.openai.api_key in gias.toml.")

        model = kwargs.pop("model", None) or self.default_embed_model or "text-embedding-3-small"
        timeout = kwargs.pop("timeout", None)

        if self._openai_client is None:
            from openai import OpenAI
            self._openai_client = OpenAI(api_key=self._openai_api_key)

        if timeout is not None:
            try:
                return self._openai_client.embeddings.create(model=model, input=text, timeout=timeout)
            except TypeError:
                return self._openai_client.embeddings.create(model=model, input=text)

        return self._openai_client.embeddings.create(model=model, input=text)

    def _wrap_provider_exception(self, e: Exception) -> Exception:
        msg = str(e).lower()
        if "rate limit" in msg or "429" in msg:
            return LLMRateLimitError(str(e))
        if "timeout" in msg or "timed out" in msg:
            return LLMTimeoutError(str(e))
        if "does not support embeddings" in msg:
            return LLMEmbeddingNotSupportedError(str(e))
        return LLMError(str(e))

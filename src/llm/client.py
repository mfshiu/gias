# src/llm/client.py

# LLMClient：統一呼叫入口（chat/json/embed）
# 唯一對外入口：client.chat() / client.json() / client.embed_text()
# 統一處理：timeout、重試、provider 呼叫、回傳標準結果（含 token/cost）
# embedding：優先走 provider.embed_text；若 provider 未支援且為 openai，則由 client 直接呼叫 OpenAI embeddings

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Protocol, Sequence, Type, TypeVar, Union, List


# ---------- Types ----------

JsonType = Union[dict, list, str, int, float, bool, None]
T = TypeVar("T")


@dataclass
class LLMUsage:
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost: Optional[float] = None  # if provider can estimate


@dataclass
class LLMResponse:
    content: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    raw: Any = None  # provider raw object (optional)


class ProviderClient(Protocol):
    """
    Provider 必須至少實作 chat(messages, **kwargs) -> response
    可選：embed_text(text, **kwargs) -> list[float]
    """

    def chat(self, messages: Sequence[Any], **kwargs: Any) -> Any:
        ...

    # optional
    def embed_text(self, text: str, **kwargs: Any) -> Any:
        ...


# ---------- Errors ----------

class LLMError(RuntimeError):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMRateLimitError(LLMError):
    pass


class LLMInvalidJSONError(LLMError):
    pass


class LLMSchemaValidationError(LLMError):
    pass


class LLMEmbeddingNotSupportedError(LLMError):
    pass


# ---------- Client ----------

SchemaType = Union[
    Dict[str, Any],         # JSON Schema (dict)
    Type[Any],              # Pydantic model class or class with model_validate / parse_obj
    Callable[[Any], Any],   # callable validator
    None,
]


class LLMClient:
    def __init__(
        self,
        provider_client: ProviderClient,
        *,
        provider_name: str = "openai",
        default_timeout: Optional[float] = 60.0,
        default_max_retries: int = 2,
        default_retry_backoff: float = 1.5,
        default_retry_jitter: float = 0.1,
        strict_json: bool = True,
        # embedding
        default_embed_model: Optional[str] = None,
        openai_api_key: Optional[str] = None,
    ):
        self.provider_client = provider_client
        self.provider_name = (provider_name or "openai").lower()

        self.default_timeout = default_timeout
        self.default_max_retries = default_max_retries
        self.default_retry_backoff = default_retry_backoff
        self.default_retry_jitter = default_retry_jitter
        self.strict_json = strict_json

        # embedding settings
        self.default_embed_model = default_embed_model or os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        self._openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")

        # lazy openai client (only if needed)
        self._openai_client = None


    @classmethod
    def from_env(cls) -> "LLMClient":
        """
        依環境變數建立 LLMClient（不做全域副作用）
        """
        provider = os.getenv("GIAS_LLM_PROVIDER", "openai").lower()

        # ---- OpenAI Provider ----
        if provider == "openai":
            from .providers.openai_provider import OpenAIProvider

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is required for OpenAI provider")

            # chat model (由 provider 使用或忽略都可)
            _chat_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            embed_model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

            provider_client = OpenAIProvider(
                api_key=api_key,
            )

        # ---- Ollama Provider ----
        elif provider == "ollama":
            from .providers.ollama_provider import OllamaProvider

            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            model = os.getenv("OLLAMA_MODEL", "llama3")

            provider_client = OllamaProvider(
                base_url=base_url,
                model=model,
            )
            embed_model = None

        # ---- Mock / Test Provider ----
        elif provider == "mock":
            from .providers.mock_provider import MockProvider

            provider_client = MockProvider()
            embed_model = None

        else:
            raise RuntimeError(f"Unknown GIAS_LLM_PROVIDER: {provider}")

        # ---- Client-level settings ----
        strict_json = os.getenv("GIAS_LLM_STRICT_JSON", "1") not in ("0", "false", "False")
        max_retries = int(os.getenv("GIAS_LLM_MAX_RETRIES", "2"))
        timeout = float(os.getenv("GIAS_LLM_TIMEOUT", "60"))

        return cls(
            provider_client=provider_client,
            provider_name=provider,
            default_timeout=timeout,
            default_max_retries=max_retries,
            strict_json=strict_json,
            default_embed_model=embed_model,
            openai_api_key=os.getenv("OPENAI_API_KEY"),
        )


    # ---- Public API ----

    def chat(self, messages: Sequence[Dict[str, Any]], **kwargs) -> LLMResponse:
        """
        直接回傳文字（與 usage/cost），不做 JSON parsing。
        """
        raw = self._call_with_retry(messages, **kwargs)
        return self._normalize_response(raw)

    def json(
        self,
        messages: Sequence[Dict[str, Any]],
        schema: SchemaType = None,
        **kwargs,
    ) -> Any:
        """
        取得模型回傳後：
        1) 解析 JSON（自動從文字中擷取 JSON 區塊）
        2) schema 驗證（JSON Schema / Pydantic / callable）
        回傳：驗證後的物件（dict/list 或 Pydantic instance 或 callable 回傳值）
        """
        raw = self._call_with_retry(messages, **kwargs)
        resp = self._normalize_response(raw)

        obj = self._parse_json(resp.content)
        validated = self._validate_schema(obj, schema)
        return validated

    def embed_text(self, text: str, **kwargs) -> List[float]:
        """
        取得真實 embedding vector（list[float]）

        優先順序：
        1) provider_client.embed_text(text, **kwargs) 若存在
        2) 若 provider_name == 'openai'，由本 client 直接呼叫 OpenAI Embeddings API
        """
        raw = self._embed_with_retry(text, **kwargs)
        vec = self._normalize_embedding(raw)
        return vec


    # ---- Retry & Provider Call (chat) ----

    def _call_with_retry(self, messages: Sequence[Dict[str, Any]], **kwargs) -> Any:
        timeout = kwargs.pop("timeout", self.default_timeout)
        max_retries = kwargs.pop("max_retries", self.default_max_retries)
        backoff = kwargs.pop("retry_backoff", self.default_retry_backoff)
        jitter = kwargs.pop("retry_jitter", self.default_retry_jitter)

        last_exc: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                if timeout is not None and "timeout" not in kwargs:
                    kwargs["timeout"] = timeout
                return self.provider_client.chat(messages, **kwargs)

            except Exception as e:
                last_exc = e
                retriable = self._is_retriable(e)
                if attempt >= max_retries or not retriable:
                    raise self._wrap_provider_exception(e) from e

                sleep_s = (backoff ** attempt)
                sleep_s = sleep_s + (jitter * (0.5 - (time.time() % 1)))
                sleep_s = max(0.0, sleep_s)
                time.sleep(sleep_s)

        raise LLMError(f"LLM call failed: {last_exc}")  # pragma: no cover


    # ---- Retry & Provider Call (embedding) ----

    def _embed_with_retry(self, text: str, **kwargs) -> Any:
        timeout = kwargs.pop("timeout", self.default_timeout)
        max_retries = kwargs.pop("max_retries", self.default_max_retries)
        backoff = kwargs.pop("retry_backoff", self.default_retry_backoff)
        jitter = kwargs.pop("retry_jitter", self.default_retry_jitter)

        last_exc: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                if timeout is not None and "timeout" not in kwargs:
                    kwargs["timeout"] = timeout

                # 1) provider 優先
                provider_embed = getattr(self.provider_client, "embed_text", None)
                if callable(provider_embed):
                    return provider_embed(text, **kwargs)

                # 2) openai fallback
                if self.provider_name == "openai":
                    return self._openai_embed(text, **kwargs)

                raise LLMEmbeddingNotSupportedError(f"Provider '{self.provider_name}' does not support embeddings.")

            except Exception as e:
                last_exc = e
                retriable = self._is_retriable(e)
                if attempt >= max_retries or not retriable:
                    raise self._wrap_provider_exception(e) from e

                sleep_s = (backoff ** attempt)
                sleep_s = sleep_s + (jitter * (0.5 - (time.time() % 1)))
                sleep_s = max(0.0, sleep_s)
                time.sleep(sleep_s)

        raise LLMError(f"Embedding call failed: {last_exc}")  # pragma: no cover

    def _openai_embed(self, text: str, **kwargs) -> Any:
        """
        OpenAI embedding 的內建 fallback（只在 provider 未實作 embed_text 時才走）
        """
        if not self._openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI embeddings.")

        model = kwargs.pop("model", None) or self.default_embed_model
        # timeout 可能是 float 秒數；openai SDK 支援 request_timeout，但不同版本參數可能不同
        timeout = kwargs.pop("timeout", None)

        if self._openai_client is None:
            from openai import OpenAI
            self._openai_client = OpenAI(api_key=self._openai_api_key)

        # 盡量用通用參數；timeout 交給底層（若 SDK 不吃也不會壞）
        if timeout is not None:
            try:
                resp = self._openai_client.embeddings.create(model=model, input=text, timeout=timeout)
            except TypeError:
                resp = self._openai_client.embeddings.create(model=model, input=text)
        else:
            resp = self._openai_client.embeddings.create(model=model, input=text)

        return resp

    def _normalize_embedding(self, raw: Any) -> List[float]:
        """
        接受形狀：
        - list[float]
        - dict: {"embedding": [...]}
        - OpenAI SDK response: resp.data[0].embedding
        """
        if isinstance(raw, list) and raw and isinstance(raw[0], (int, float)):
            return [float(x) for x in raw]

        if isinstance(raw, dict) and "embedding" in raw and isinstance(raw["embedding"], list):
            return [float(x) for x in raw["embedding"]]

        # OpenAI-like object
        try:
            data = getattr(raw, "data", None)
            if data and len(data) > 0:
                emb = getattr(data[0], "embedding", None)
                if isinstance(emb, list):
                    return [float(x) for x in emb]
        except Exception:
            pass

        raise LLMError("Failed to normalize embedding response.")


    def _is_retriable(self, e: Exception) -> bool:
        msg = str(e).lower()
        retriable_keywords = [
            "rate limit",
            "429",
            "timeout",
            "timed out",
            "temporarily",
            "temporary",
            "overloaded",
            "connection reset",
            "connection aborted",
            "service unavailable",
            "503",
        ]
        return any(k in msg for k in retriable_keywords)

    def _wrap_provider_exception(self, e: Exception) -> LLMError:
        msg = str(e).lower()
        if "rate limit" in msg or "429" in msg:
            return LLMRateLimitError(str(e))
        if "timeout" in msg or "timed out" in msg:
            return LLMTimeoutError(str(e))
        if "does not support embeddings" in msg:
            return LLMEmbeddingNotSupportedError(str(e))
        return LLMError(str(e))

    # ---- Response Normalization ----

    def _normalize_response(self, raw: Any) -> LLMResponse:
        """
        允許 provider 回傳：
        - LLMResponse
        - 任何有 .content 的物件
        - dict: {"content": "...", "usage": {...}}
        """
        if isinstance(raw, LLMResponse):
            return raw

        if isinstance(raw, dict):
            content = raw.get("content", "") or ""
            usage_dict = raw.get("usage") or {}
            usage = self._usage_from_any(usage_dict)
            return LLMResponse(content=content, usage=usage, raw=raw)

        content = getattr(raw, "content", None)
        if content is None:
            content = self._best_effort_extract_content(raw)

        usage_any = getattr(raw, "usage", None)
        usage = self._usage_from_any(usage_any)

        return LLMResponse(content=str(content or ""), usage=usage, raw=raw)

    def _best_effort_extract_content(self, raw: Any) -> str:
        try:
            choices = getattr(raw, "choices", None) or (raw.get("choices") if isinstance(raw, dict) else None)
            if choices:
                c0 = choices[0]
                msg = getattr(c0, "message", None) or (c0.get("message") if isinstance(c0, dict) else None)
                if msg:
                    return str(getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else "") or "")
                return str(getattr(c0, "text", None) or (c0.get("text") if isinstance(c0, dict) else "") or "")
        except Exception:
            pass
        return ""

    def _usage_from_any(self, usage_any: Any) -> LLMUsage:
        if usage_any is None:
            return LLMUsage()

        if isinstance(usage_any, LLMUsage):
            return usage_any

        if isinstance(usage_any, dict):
            return LLMUsage(
                prompt_tokens=usage_any.get("prompt_tokens"),
                completion_tokens=usage_any.get("completion_tokens"),
                total_tokens=usage_any.get("total_tokens"),
                cost=usage_any.get("cost"),
            )

        return LLMUsage(
            prompt_tokens=getattr(usage_any, "prompt_tokens", None),
            completion_tokens=getattr(usage_any, "completion_tokens", None),
            total_tokens=getattr(usage_any, "total_tokens", None),
            cost=getattr(usage_any, "cost", None),
        )

    # ---- JSON Parsing ----

    def _parse_json(self, content: str) -> JsonType:
        text = (content or "").strip()

        try:
            return json.loads(text)
        except Exception:
            pass

        fenced = self._extract_fenced_json(text)
        if fenced is not None:
            try:
                return json.loads(fenced)
            except Exception:
                pass

        candidate = self._extract_first_json_object(text)
        if candidate is not None:
            try:
                return json.loads(candidate)
            except Exception:
                pass

        if self.strict_json:
            raise LLMInvalidJSONError("Model output is not valid JSON.")
        return {"_raw": text}

    def _extract_fenced_json(self, text: str) -> Optional[str]:
        m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None

    def _extract_first_json_object(self, text: str) -> Optional[str]:
        start_positions = [(text.find("{"), "{"), (text.find("["), "[")]
        start_positions = [(pos, ch) for pos, ch in start_positions if pos != -1]
        if not start_positions:
            return None

        pos, ch = min(start_positions, key=lambda x: x[0])
        closing = "}" if ch == "{" else "]"

        depth = 0
        in_str = False
        esc = False

        for i in range(pos, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue

            if c == '"':
                in_str = True
                continue

            if c == ch:
                depth += 1
            elif c == closing:
                depth -= 1
                if depth == 0:
                    return text[pos : i + 1].strip()

        return None

    # ---- Schema Validation ----

    def _validate_schema(self, obj: Any, schema: SchemaType) -> Any:
        if schema is None:
            return obj

        if callable(schema) and not isinstance(schema, dict) and not isinstance(schema, type):
            try:
                return schema(obj)
            except Exception as e:
                raise LLMSchemaValidationError(str(e)) from e

        if isinstance(schema, type):
            if hasattr(schema, "model_validate"):
                try:
                    return schema.model_validate(obj)  # type: ignore[attr-defined]
                except Exception as e:
                    raise LLMSchemaValidationError(str(e)) from e
            if hasattr(schema, "parse_obj"):
                try:
                    return schema.parse_obj(obj)  # type: ignore[attr-defined]
                except Exception as e:
                    raise LLMSchemaValidationError(str(e)) from e

        if isinstance(schema, dict):
            try:
                self._validate_json_schema_minimal(obj, schema)
                return obj
            except Exception as e:
                raise LLMSchemaValidationError(str(e)) from e

        raise LLMSchemaValidationError(f"Unsupported schema type: {type(schema)}")

    def _validate_json_schema_minimal(self, obj: Any, schema: Dict[str, Any]) -> None:
        expected_type = schema.get("type")
        if expected_type:
            if expected_type == "object" and not isinstance(obj, dict):
                raise ValueError("Expected object.")
            if expected_type == "array" and not isinstance(obj, list):
                raise ValueError("Expected array.")
            if expected_type == "string" and not isinstance(obj, str):
                raise ValueError("Expected string.")
            if expected_type == "number" and not isinstance(obj, (int, float)):
                raise ValueError("Expected number.")
            if expected_type == "integer" and not isinstance(obj, int):
                raise ValueError("Expected integer.")
            if expected_type == "boolean" and not isinstance(obj, bool):
                raise ValueError("Expected boolean.")

        if isinstance(obj, dict):
            required = schema.get("required") or []
            for k in required:
                if k not in obj:
                    raise ValueError(f"Missing required field: {k}")

            props = schema.get("properties") or {}
            for k, sub_schema in props.items():
                if k in obj and isinstance(sub_schema, dict):
                    self._validate_json_schema_minimal(obj[k], sub_schema)

        if isinstance(obj, list):
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for item in obj:
                    self._validate_json_schema_minimal(item, item_schema)


# ---- Optional: helper to build client from env (safe, no side effects) ----

def build_llm_client(provider_client: ProviderClient) -> LLMClient:
    strict_json = os.getenv("GIAS_LLM_STRICT_JSON", "1") not in ("0", "false", "False")
    max_retries = int(os.getenv("GIAS_LLM_MAX_RETRIES", "2"))
    timeout = float(os.getenv("GIAS_LLM_TIMEOUT", "60"))
    provider_name = os.getenv("GIAS_LLM_PROVIDER", "openai").lower()

    return LLMClient(
        provider_client=provider_client,
        provider_name=provider_name,
        default_timeout=timeout,
        default_max_retries=max_retries,
        strict_json=strict_json,
        default_embed_model=os.getenv("OPENAI_EMBED_MODEL"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
    )

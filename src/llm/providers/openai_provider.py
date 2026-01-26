# src/llm/providers/openai_provider.py
#
# OpenAIProvider（或你用的本地/雲端）
# 實作 BaseProvider 抽象介面
#
# 依 OpenAI 官方文件：建議新專案用 Responses API；Chat Completions 仍可用。 :contentReference[oaicite:0]{index=0}
# 本 provider 預設用 Chat Completions（最貼近 messages 介面），也可用環境變數切到 Responses。

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Sequence

from openai import OpenAI

from .base import BaseProvider, Message, ProviderResponse, ProviderUsage


class OpenAIProvider(BaseProvider):
    """
    OpenAIProvider: 封裝 OpenAI Python SDK，提供統一 chat(messages, ...) 介面

    Env vars (optional):
      - OPENAI_API_KEY
      - OPENAI_BASE_URL
      - OPENAI_ORG
      - OPENAI_PROJECT
      - GIAS_OPENAI_USE_RESPONSES=1   # 1/true 走 Responses API；否則走 Chat Completions
    """

    name: str = "openai"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        organization: Optional[str] = None,
        project: Optional[str] = None,
        use_responses: Optional[bool] = None,
        default_model: str = "gpt-4.1-mini",
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self.organization = organization or os.getenv("OPENAI_ORG")
        self.project = project or os.getenv("OPENAI_PROJECT")
        self.default_model = default_model

        if use_responses is None:
            use_responses = os.getenv("GIAS_OPENAI_USE_RESPONSES", "0").lower() in ("1", "true", "yes")
        self.use_responses = use_responses

        # OpenAI() 會從環境變數讀 key；也可顯式傳入
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            organization=self.organization,
            project=self.project,
        )

    def chat(
        self,
        messages: Sequence[Message],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        top_p: Optional[float] = None,
        seed: Optional[int] = None,
        stop: Optional[Sequence[str]] = None,
        response_format: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> ProviderResponse:
        model = model or self.default_model

        if self.use_responses:
            return self._chat_via_responses(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                top_p=top_p,
                seed=seed,
                stop=stop,
                response_format=response_format,
                **kwargs,
            )

        return self._chat_via_chat_completions(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            top_p=top_p,
            seed=seed,
            stop=stop,
            response_format=response_format,
            **kwargs,
        )

    # -----------------------
    # Chat Completions API
    # -----------------------

    def _chat_via_chat_completions(
        self,
        *,
        messages: Sequence[Message],
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        timeout: Optional[float],
        top_p: Optional[float],
        seed: Optional[int],
        stop: Optional[Sequence[str]],
        response_format: Optional[Dict[str, Any]],
        **kwargs: Any,
    ) -> ProviderResponse:
        # Chat Completions 參考：messages + model :contentReference[oaicite:1]{index=1}
        payload: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
        }

        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if top_p is not None:
            payload["top_p"] = top_p
        if seed is not None:
            payload["seed"] = seed
        if stop is not None:
            payload["stop"] = list(stop)
        if response_format is not None:
            # e.g. {"type": "json_object"} (JSON mode) 或 Structured Outputs
            payload["response_format"] = response_format

        # 允許外部傳入 tools / tool_choice / etc
        payload.update(kwargs)

        # Python SDK：timeout 可直接作為 create() 的參數（requests timeout）
        if timeout is not None:
            payload["timeout"] = timeout

        resp = self.client.chat.completions.create(**payload)

        content = ""
        try:
            content = (resp.choices[0].message.content or "").strip()
        except Exception:
            # 保底：轉字串
            content = str(resp)

        usage = ProviderUsage(
            prompt_tokens=getattr(resp.usage, "prompt_tokens", None) if getattr(resp, "usage", None) else None,
            completion_tokens=getattr(resp.usage, "completion_tokens", None) if getattr(resp, "usage", None) else None,
            total_tokens=getattr(resp.usage, "total_tokens", None) if getattr(resp, "usage", None) else None,
            cost=None,
        )

        return ProviderResponse(
            content=content,
            usage=usage,
            raw=resp,
            model=model,
            provider=self.name,
        )

    # -----------------------
    # Responses API
    # -----------------------

    def _chat_via_responses(
        self,
        *,
        messages: Sequence[Message],
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        timeout: Optional[float],
        top_p: Optional[float],
        seed: Optional[int],
        stop: Optional[Sequence[str]],
        response_format: Optional[Dict[str, Any]],
        **kwargs: Any,
    ) -> ProviderResponse:
        # Responses API 是新 primitive：input 支援 role/content 的訊息陣列 :contentReference[oaicite:2]{index=2}
        payload: Dict[str, Any] = {
            "model": model,
            "input": list(messages),
        }

        # Responses 的參數命名可能與 chat 不同；這裡採「能用就帶」策略，無效時由 SDK 報錯
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_output_tokens"] = max_tokens  # Responses 常用 max_output_tokens
        if top_p is not None:
            payload["top_p"] = top_p
        if seed is not None:
            payload["seed"] = seed
        if stop is not None:
            payload["stop"] = list(stop)

        # 有些人會混用 response_format；Responses 不一定接受
        # 若你確定要用 JSON mode，建議在 prompt 端要求輸出 JSON，並由 LLMClient 解析驗證。 :contentReference[oaicite:3]{index=3}
        if response_format is not None:
            payload["response_format"] = response_format

        payload.update(kwargs)

        if timeout is not None:
            payload["timeout"] = timeout

        resp = self.client.responses.create(**payload)

        # 官方 SDK 提供 output_text 方便取純文字
        content = getattr(resp, "output_text", None)
        if content is None:
            # 保底：嘗試從 output 結構擷取
            content = self._best_effort_extract_output_text(resp)
        content = (content or "").strip()

        usage_obj = getattr(resp, "usage", None)
        usage = ProviderUsage(
            prompt_tokens=getattr(usage_obj, "input_tokens", None) if usage_obj else None,
            completion_tokens=getattr(usage_obj, "output_tokens", None) if usage_obj else None,
            total_tokens=getattr(usage_obj, "total_tokens", None) if usage_obj else None,
            cost=None,
        )

        return ProviderResponse(
            content=content,
            usage=usage,
            raw=resp,
            model=model,
            provider=self.name,
        )

    def _best_effort_extract_output_text(self, resp: Any) -> str:
        """
        Responses 的 output 結構可能含多段 content；此函式做保底擷取。
        """
        try:
            output = getattr(resp, "output", None)
            if not output:
                return ""
            texts = []
            for item in output:
                content = getattr(item, "content", None)
                if not content:
                    continue
                for c in content:
                    t = getattr(c, "text", None)
                    if t:
                        texts.append(t)
            return "\n".join(texts)
        except Exception:
            return ""

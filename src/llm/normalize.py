# src/llm/normalize.py
from __future__ import annotations

from typing import Any

from .types import LLMResponse, LLMUsage


def usage_from_any(usage_any: Any) -> LLMUsage:
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


def best_effort_extract_content(raw: Any) -> str:
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


def normalize_response(raw: Any) -> LLMResponse:
    if isinstance(raw, LLMResponse):
        return raw

    if isinstance(raw, dict):
        content = raw.get("content", "") or ""
        usage_dict = raw.get("usage") or {}
        usage = usage_from_any(usage_dict)
        return LLMResponse(content=content, usage=usage, raw=raw)

    content = getattr(raw, "content", None)
    if content is None:
        content = best_effort_extract_content(raw)

    usage_any = getattr(raw, "usage", None)
    usage = usage_from_any(usage_any)

    return LLMResponse(content=str(content or ""), usage=usage, raw=raw)

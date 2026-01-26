# src/llm/providers/base.py
# BaseProvider：抽象介面
# 定義 LLM Provider 的基本行為
# 讓不同 LLM Provider 可以互換使用
# 例如 OpenAIProvider / OllamaProvider / CustomProvider

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Sequence, TypedDict, runtime_checkable


class Message(TypedDict, total=False):
    role: str                # "system" | "user" | "assistant" | "tool"
    content: str
    name: str
    tool_call_id: str
    tool_calls: Any
    # 你若有需要可補：metadata / attachments / etc.


@dataclass
class ProviderUsage:
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost: Optional[float] = None  # 若 provider 可估算/回報成本


@dataclass
class ProviderResponse:
    """
    統一的 provider 回傳格式（建議 provider 都回這個）
    若 provider 回傳 SDK 原生物件，也至少要有 content 屬性讓 LLMClient 能抽取。
    """
    content: str
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    raw: Any = None               # 原生回傳（選用）
    model: Optional[str] = None   # 實際使用的模型（選用）
    provider: Optional[str] = None  # provider 名稱（選用）


@runtime_checkable
class BaseProvider(Protocol):
    """
    LLM Provider 抽象介面（Protocol）
    你可以用繼承實作，也可以直接寫 class 只要符合方法簽名即可。
    """

    name: str  # e.g. "openai", "ollama"

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
        """
        統一聊天介面：
        - messages: OpenAI-style messages
        - model: 模型名稱（provider 可忽略並使用預設）
        - response_format: 用於 JSON mode 等（若 provider 支援）
        - kwargs: 保留給 provider 自訂參數（例如 base_url / headers / tools 等）
        """
        ...


def is_provider(obj: Any) -> bool:
    """
    runtime 小工具：檢查物件是否符合 BaseProvider
    """
    return isinstance(obj, BaseProvider)

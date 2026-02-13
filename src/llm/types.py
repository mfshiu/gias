# src/llm/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Sequence, Type, TypeVar, Union, Callable

JsonType = Union[dict, list, str, int, float, bool, None]
T = TypeVar("T")


@dataclass
class LLMUsage:
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost: Optional[float] = None


@dataclass
class LLMResponse:
    content: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    raw: Any = None


class ProviderClient(Protocol):
    """Provider 最小契約：只要能 chat。"""
    def chat(self, messages: Sequence[Any], **kwargs: Any) -> Any:
        ...


class EmbeddingProviderClient(Protocol):
    """可選：若 provider 自己支援 embedding，才需要實作。"""
    def embed_text(self, text: str, **kwargs: Any) -> Any:
        ...


SchemaType = Union[
    Dict[str, Any],
    Type[Any],
    Callable[[Any], Any],
    None,
]

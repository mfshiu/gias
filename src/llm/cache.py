# src/llm/cache.py
"""
Embedding 快取：同一個模型對同一段文字算出的向量是固定的，算過就不必再呼叫 API。

- key：provider、embedding 模型、影響結果的參數、文字
- LRU，最多 max_items 筆；不需要時效
- 同一個 process 內的 LLMClient 共用一份（shared_embedding_cache），例如 benchmark 每個案例
  建立新的 IntentionalAgent 時，相同的子意圖不必重算
- 只快取 embedding；對話（chat）的回應不快取（重新規劃時需要依新的脈絡得到不同結果）
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Hashable, Sequence


class EmbeddingCache:
    def __init__(self, max_items: int = 2048):
        self.max_items = int(max_items)
        self._items: OrderedDict[Hashable, tuple[float, ...]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(provider: str, model: str | None, text: str, options: dict[str, Any] | None = None) -> Hashable:
        extra = tuple(sorted((str(k), repr(v)) for k, v in (options or {}).items()))
        return (provider, model or "", extra, text)

    def get(self, key: Hashable) -> list[float] | None:
        if self.max_items <= 0:
            return None
        with self._lock:
            vec = self._items.get(key)
            if vec is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return list(vec)

    def put(self, key: Hashable, vector: Sequence[float]) -> None:
        if self.max_items <= 0:
            return
        with self._lock:
            self._items[key] = tuple(vector)
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def resize(self, max_items: int) -> None:
        with self._lock:
            self.max_items = int(max_items)
            while len(self._items) > max(self.max_items, 0):
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.hits = self.misses = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


_shared: EmbeddingCache | None = None
_shared_lock = threading.Lock()


def shared_embedding_cache(max_items: int = 2048) -> EmbeddingCache:
    """process 內共用的 embedding 快取；容量取各設定中最大者。"""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = EmbeddingCache(max_items)
        elif max_items > _shared.max_items:
            _shared.resize(max_items)
        return _shared

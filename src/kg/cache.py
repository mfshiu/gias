"""KG 查詢結果的快取（有時效）。

Action KG 只在執行 seed 時改變；規劃時同一個 Action 的參數、輸出、前置條件會被重複查詢
（比對、選動作、每個步驟、每次重規劃），以快取避免重複往返。
時效到期後重新查詢，讓常駐服務在重新 seed 後最晚 ttl 秒內看到新資料；ttl ≤ 0 表示不快取。
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any, Hashable

_MISSING = object()


class TTLCache:
    def __init__(self, ttl_sec: float = 300.0):
        self.ttl_sec = float(ttl_sec)
        self._items: dict[Hashable, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> tuple[bool, Any]:
        """回傳 (是否命中, 值的副本)。"""
        if self.ttl_sec <= 0:
            return False, None
        with self._lock:
            expires_at, value = self._items.get(key, (0.0, _MISSING))
            if value is _MISSING or time.monotonic() >= expires_at:
                self._items.pop(key, None)
                return False, None
        return True, copy.deepcopy(value)

    def put(self, key: Hashable, value: Any) -> None:
        if self.ttl_sec <= 0:
            return
        with self._lock:
            self._items[key] = (time.monotonic() + self.ttl_sec, copy.deepcopy(value))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

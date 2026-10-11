"""
意圖的持久化。

- InMemoryIntentionStore：只放在記憶體（測試、沒有 Blackboard 時）
- BlackboardIntentionStore：寫成 Blackboard KG 的 (:Intention {manager, intention_id}) 節點，可查詢與稽核。
  在背景 thread 依序寫入，黑板代理慢或不在線時不會拖住意圖處理。
  啟動時把同一個 manager 尚未結束的意圖標為 interrupted（不自動續做）。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Callable, Protocol

from .model import TERMINAL_STATUSES, Intention, IntentionStatus

_UPSERT = """
MERGE (i:Intention {manager: $manager, intention_id: $intention_id})
SET i.text = $text, i.priority = $priority, i.requester = $requester, i.status = $status,
    i.reason = $reason, i.attempts = $attempts, i.created_at = $created_at, i.updated_at = $updated_at,
    i.plan_steps = $plan_steps, i.result = $result, i.history = $history
"""

_MARK_INTERRUPTED = """
MATCH (i:Intention {manager: $manager})
WHERE NOT i.status IN $terminal
SET i.status = 'interrupted', i.reason = $reason, i.updated_at = $now
"""


class IntentionStore(Protocol):
    def save(self, intention: Intention) -> None: ...

    def mark_unfinished_interrupted(self, reason: str) -> None: ...

    def close(self) -> None: ...


class InMemoryIntentionStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def save(self, intention: Intention) -> None:
        with self._lock:
            self.records[intention.intention_id] = intention.to_dict()

    def mark_unfinished_interrupted(self, reason: str) -> None:
        terminal = {s.value for s in TERMINAL_STATUSES}
        with self._lock:
            for record in self.records.values():
                if record["status"] not in terminal:
                    record.update(status=IntentionStatus.INTERRUPTED.value, reason=reason, updated_at=time.time())

    def close(self) -> None:
        pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class BlackboardIntentionStore:
    """write(cypher, params) -> bool 由呼叫端提供（例：經 blackboard.control 的 write 命令）。"""

    _STOP = object()

    def __init__(
        self,
        write: Callable[[str, dict[str, Any]], bool],
        *,
        manager: str,
        logger: logging.Logger | None = None,
    ):
        self._write = write
        self.manager = manager
        self.logger = logger or logging.getLogger(__name__)
        self._queue: queue.Queue = queue.Queue()
        self._warned = False
        self._thread = threading.Thread(target=self._run, name="BlackboardIntentionStore", daemon=True)
        self._thread.start()

    def save(self, intention: Intention) -> None:
        d = intention.to_dict()
        self._queue.put((_UPSERT, {
            "manager": self.manager,
            "intention_id": d["intention_id"],
            "text": d["text"],
            "priority": d["priority"],
            "requester": d["requester"],
            "status": d["status"],
            "reason": d["reason"],
            "attempts": d["attempts"],
            "created_at": d["created_at"],
            "updated_at": d["updated_at"],
            # Neo4j 屬性不能是 map 或 map 的清單，改存 JSON 字串
            "plan_steps": _json(d["plan_steps"]),
            "result": _json(d["result"]),
            "history": _json(d["history"]),
        }))

    def mark_unfinished_interrupted(self, reason: str) -> None:
        self._queue.put((_MARK_INTERRUPTED, {
            "manager": self.manager,
            "terminal": sorted(s.value for s in TERMINAL_STATUSES),
            "reason": reason,
            "now": time.time(),
        }))

    def flush(self, timeout: float = 5.0) -> bool:
        """等待排隊中的寫入完成；逾時回傳 False。"""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    def close(self, timeout: float = 5.0) -> None:
        self.flush(timeout)
        self._queue.put(self._STOP)
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._STOP:
                    return
                cypher, params = item
                ok = False
                try:
                    ok = bool(self._write(cypher, params))
                except Exception as e:
                    self.logger.debug("Intention store write raised: %s", e)
                if not ok:
                    log = self.logger.debug if self._warned else self.logger.warning
                    log("Intention store write failed (is BlackboardAgent running?)")
                    self._warned = True
            finally:
                self._queue.task_done()

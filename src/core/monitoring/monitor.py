"""
ExecutionMonitor：把多種非同步訊號（黑板事件、action 結果、進度）
匯流到單一 thread-safe queue，供 IntentionalAgent 的監測迴圈 drain。

設計：
- monitor 本身不訂閱 broker / 不解析業務語意；它只是 message bus 上的 inbox。
- IntentionalAgent 在 on_connected 註冊 subscribe()，把 callback 轉呼叫 monitor.on_*。
- 任何時刻可呼叫 drain_changes() / drain_results() 取出已累積的事件；
  也可用 wait_for_signal(timeout) 阻塞等待至少一個事件（給監測迴圈使用）。
"""

from __future__ import annotations

import logging
import threading
import time
from queue import Empty, Queue
from typing import Any

from .events import ActionResult, EnvChange


class ExecutionMonitor:
    """非同步訊號聚合器。"""

    def __init__(self, *, logger: logging.Logger | None = None):
        self._env_queue: Queue[EnvChange] = Queue()
        self._result_queue: Queue[ActionResult] = Queue()
        self._signal_event = threading.Event()
        self._task_to_node: dict[str, str] = {}
        self._lock = threading.Lock()
        self.logger = logger or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Task <-> Node binding（IA 在 dispatch 時呼叫）
    # ------------------------------------------------------------------
    def bind_task(self, task_id: str, node_id: str) -> None:
        with self._lock:
            self._task_to_node[task_id] = node_id

    def unbind_task(self, task_id: str) -> None:
        with self._lock:
            self._task_to_node.pop(task_id, None)

    def resolve_node_id(self, task_id: str) -> str:
        with self._lock:
            return self._task_to_node.get(task_id, "")

    # ------------------------------------------------------------------
    # Producer hooks
    # ------------------------------------------------------------------
    def on_env_event(self, topic: str, payload: Any) -> None:
        """訂閱 `blackboard.subscriber.<id>` 時的 callback。

        payload 可能是 dict（BlackboardEvent.to_dict()）或 parcel 物件。
        """
        data = _extract_dict(payload)
        change = EnvChange.from_blackboard_event_dict(data)
        self._env_queue.put(change)
        self._signal_event.set()
        self.logger.debug(
            "ExecutionMonitor env event: topic=%s action=%s", change.topic, change.action
        )

    def on_action_result(self, topic: str, payload: Any) -> None:
        """訂閱 `info.result` / `navigation.result` 時的 callback。"""
        data = _extract_dict(payload)
        task_id = str(data.get("task_id", "")) if isinstance(data, dict) else ""
        if not task_id:
            self.logger.warning("ExecutionMonitor action result missing task_id; ignored.")
            return
        with self._lock:
            node_id = self._task_to_node.get(task_id, "")
        ar = ActionResult.from_payload(data, node_id=node_id)
        self._result_queue.put(ar)
        self._signal_event.set()
        self.logger.debug(
            "ExecutionMonitor action result: task=%s ok=%s cancelled=%s",
            task_id,
            ar.ok,
            ar.cancelled,
        )

    def on_progress(self, topic: str, payload: Any) -> None:
        """進度通知；目前只記 log，不入 queue（advisory only）。"""
        data = _extract_dict(payload)
        if isinstance(data, dict):
            self.logger.debug(
                "ExecutionMonitor progress: task=%s state=%s ratio=%s",
                data.get("task_id"),
                data.get("state"),
                data.get("ratio"),
            )

    # ------------------------------------------------------------------
    # Consumer hooks（IA 監測迴圈）
    # ------------------------------------------------------------------
    def drain_changes(self) -> list[EnvChange]:
        out: list[EnvChange] = []
        while True:
            try:
                out.append(self._env_queue.get_nowait())
            except Empty:
                break
        return out

    def drain_results(self) -> list[ActionResult]:
        out: list[ActionResult] = []
        while True:
            try:
                out.append(self._result_queue.get_nowait())
            except Empty:
                break
        return out

    def wait_for_signal(self, timeout: float) -> bool:
        """阻塞等到至少一個訊號或 timeout。

        - 返回 True 表示期間有新事件抵達（或 queue 仍有東西）
        - 返回 False 表示 timeout
        - 不會清空 queue
        """
        if self._has_pending():
            return True
        triggered = self._signal_event.wait(timeout)
        # consume signal flag
        self._signal_event.clear()
        return triggered or self._has_pending()

    def _has_pending(self) -> bool:
        return not (self._env_queue.empty() and self._result_queue.empty())


def _extract_dict(payload: Any) -> dict[str, Any]:
    """從可能的 Parcel / dict / 字串中取出 dict。"""
    if payload is None:
        return {}
    content = getattr(payload, "content", payload)
    if isinstance(content, dict):
        return content
    if isinstance(content, (bytes, bytearray)):
        try:
            import json

            return json.loads(content)
        except Exception:
            return {}
    if isinstance(content, str):
        try:
            import json

            return json.loads(content)
        except Exception:
            return {}
    return {}

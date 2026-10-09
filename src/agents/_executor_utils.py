"""Agent 共用：topic 常數、解析 payload、組裝請求/結果。

擴充支援監測 v1 protocol（見 docs/monitoring_protocol.md）：
- task_id / idempotency_key / interruptible / side_effect / deadline_sec
- info.result / navigation.result / info.cancel / navigation.cancel / info.progress / navigation.progress

向後相容：
- 舊呼叫 `build_action_payload(task, params, ...)` 仍可用；新欄位皆可選。
- 舊 executor 的 `_handle(topic, pcl)` 回傳 dict 行為仍保留（auto-reply via topic_return）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

# ----------------------------------------------------------------------
# Topic 常數
# ----------------------------------------------------------------------
TOPIC_INFO_REQUEST = "info.request"
TOPIC_NAVIGATION_REQUEST = "navigation.request"
TOPIC_INFO_RESULT = "info.result"
TOPIC_NAVIGATION_RESULT = "navigation.result"
TOPIC_INFO_CANCEL = "info.cancel"
TOPIC_NAVIGATION_CANCEL = "navigation.cancel"
TOPIC_INFO_PROGRESS = "info.progress"
TOPIC_NAVIGATION_PROGRESS = "navigation.progress"

# request topic 對應的 channel base（result/cancel/progress 從這裡推導）
_TOPIC_MAP = {
    "info": TOPIC_INFO_REQUEST,
    "navigation": TOPIC_NAVIGATION_REQUEST,
}

# 反查：從 channel 名 → 各 topic
CHANNEL_TOPICS: dict[str, dict[str, str]] = {
    "info": {
        "request": TOPIC_INFO_REQUEST,
        "result": TOPIC_INFO_RESULT,
        "cancel": TOPIC_INFO_CANCEL,
        "progress": TOPIC_INFO_PROGRESS,
    },
    "navigation": {
        "request": TOPIC_NAVIGATION_REQUEST,
        "result": TOPIC_NAVIGATION_RESULT,
        "cancel": TOPIC_NAVIGATION_CANCEL,
        "progress": TOPIC_NAVIGATION_PROGRESS,
    },
}


def resolve_request_topic(topic: str | None) -> str:
    """將 topic 簡稱或 DB 值解析為實際的 request topic。"""
    if not topic:
        return TOPIC_INFO_REQUEST  # fallback
    t = (topic or "").strip().lower()
    if t in _TOPIC_MAP:
        return _TOPIC_MAP[t]
    if ".request" in t:
        return topic.strip()
    return TOPIC_INFO_REQUEST


def channel_of_request_topic(topic: str) -> str:
    """從 request topic 推回 channel 名（info / navigation）。"""
    t = (topic or "").strip().lower()
    if t.startswith("navigation"):
        return "navigation"
    return "info"


def result_topic_for(request_topic: str) -> str:
    return CHANNEL_TOPICS[channel_of_request_topic(request_topic)]["result"]


def cancel_topic_for(request_topic: str) -> str:
    return CHANNEL_TOPICS[channel_of_request_topic(request_topic)]["cancel"]


def progress_topic_for(request_topic: str) -> str:
    return CHANNEL_TOPICS[channel_of_request_topic(request_topic)]["progress"]


# ----------------------------------------------------------------------
# Payload builders
# ----------------------------------------------------------------------
def build_action_payload(
    task: str,
    params: dict[str, Any],
    action_id: Any = None,
    intent: str = "",
    *,
    task_id: str | None = None,
    idempotency_key: str | None = None,
    deadline_sec: float | None = None,
    interruptible: bool | None = None,
    side_effect: str | None = None,
) -> dict[str, Any]:
    """組裝 action 請求 payload。

    舊欄位：task / params / action_id / intent（必要）
    新欄位（皆可選）：task_id / idempotency_key / deadline_sec / interruptible / side_effect
    """
    payload: dict[str, Any] = {
        "task": task,
        "params": params or {},
        "action_id": action_id,
        "intent": intent or "",
    }
    if task_id is not None:
        payload["task_id"] = task_id
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    if deadline_sec is not None:
        payload["deadline_sec"] = float(deadline_sec)
    if interruptible is not None:
        payload["interruptible"] = bool(interruptible)
    if side_effect is not None:
        payload["side_effect"] = str(side_effect)
    return payload


def parse_action_payload(pcl: Any) -> dict[str, Any]:
    """從 parcel 解析 payload：{task, params, action_id, intent, [task_id, ...]}"""
    content = getattr(pcl, "content", pcl) if pcl is not None else {}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {}
    return content if isinstance(content, dict) else {}


def build_result(
    task: str,
    message: str,
    action_id: Any,
    intent: str,
    *,
    task_id: str | None = None,
    ok: bool = True,
    cancelled: bool = False,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    elapsed_sec: float | None = None,
) -> dict[str, Any]:
    """組裝執行結果。

    向後相容：未指定 task_id 時，回傳結構與舊版相同（加上 ok 預設 True）。
    """
    payload: dict[str, Any] = {
        "ok": ok,
        "task": task,
        "message": message,
        "simulated": True,
        "action_id": action_id,
        "intent": intent,
    }
    if task_id is not None:
        payload["task_id"] = task_id
    if cancelled:
        payload["cancelled"] = True
    if error is not None:
        payload["error"] = error
    if result is not None:
        payload["result"] = result
    if started_at is not None:
        payload["started_at"] = started_at
    if finished_at is not None:
        payload["finished_at"] = finished_at
    if elapsed_sec is not None:
        payload["elapsed_sec"] = float(elapsed_sec)
    return payload


def build_cancel_payload(task_id: str, *, reason: str = "") -> dict[str, Any]:
    return {"task_id": str(task_id), "reason": str(reason or "")}


def parse_cancel_payload(pcl: Any) -> dict[str, Any]:
    return parse_action_payload(pcl)


def build_progress_payload(
    task_id: str,
    *,
    state: str = "running",
    ratio: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"task_id": str(task_id), "state": str(state)}
    if ratio is not None:
        payload["ratio"] = float(ratio)
    if note:
        payload["note"] = str(note)
    return payload


def new_task_id(prefix: str = "task") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# 未綁定 task 時 payload 使用的佔位值
UNKNOWN_TASK = "Unknown"


def is_missing_task(task: Any) -> bool:
    """task 未綁定（None、空字串或佔位值 "Unknown"）時回傳 True。

    這類請求沒有任何 executor 能真正執行，必須視為失敗，不可回報成功。
    """
    return not isinstance(task, str) or not task.strip() or task.strip() == UNKNOWN_TASK


# ----------------------------------------------------------------------
# CancelToken：跨 thread 的中斷旗標（不依賴 threading.Event 以方便 mock）
# ----------------------------------------------------------------------
class CancelToken:
    """簡單的可重設旗標。executor 端可定期 check_should_continue。"""

    def __init__(self) -> None:
        self._cancelled = False
        self._reason: str = ""

    def cancel(self, reason: str = "") -> None:
        self._cancelled = True
        if reason and not self._reason:
            self._reason = reason

    def reset(self) -> None:
        self._cancelled = False
        self._reason = ""

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def reason(self) -> str:
        return self._reason

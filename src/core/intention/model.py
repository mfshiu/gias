"""
Intention：可跨時間追蹤的意圖。

生命週期（Bratman：意圖一旦承諾就持續追求，直到達成、失敗、被放棄，或被更重要的意圖暫時擱置）：

    PENDING ──► PLANNING ──► EXECUTING ──► ACHIEVED
       │            │  │          │  │
       │            │  │          │  └──► FAILED
       │            │  └──► FAILED│
       │            ▼             ▼
       │        SUSPENDED ◄───────┘      （被高優先序意圖搶占；之後重新規劃續做）
       │            │
       └────────────┴──► DROPPED        （要求者取消）

    任何未結束的狀態 ──► INTERRUPTED     （系統關閉或重啟時尚未完成）
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# MQTT 介面（見 docs/intention_protocol.md）
TOPIC_INTENTION_SUBMIT = "intention.submit"
TOPIC_INTENTION_CANCEL = "intention.cancel"
TOPIC_INTENTION_QUERY = "intention.query"
TOPIC_INTENTION_STATUS = "intention.status"


class IntentionStatus(str, Enum):
    PENDING = "pending"            # 已提出，排隊等待
    PLANNING = "planning"          # 規劃中
    EXECUTING = "executing"        # 已承諾計畫，執行中
    SUSPENDED = "suspended"        # 被高優先序意圖搶占，等待重新規劃續做
    ACHIEVED = "achieved"
    FAILED = "failed"              # 規劃或執行失敗
    DROPPED = "dropped"            # 要求者取消
    INTERRUPTED = "interrupted"    # 系統關閉或重啟時尚未完成


TERMINAL_STATUSES = frozenset({
    IntentionStatus.ACHIEVED,
    IntentionStatus.FAILED,
    IntentionStatus.DROPPED,
    IntentionStatus.INTERRUPTED,
})

_S = IntentionStatus
_ALLOWED: dict[IntentionStatus, frozenset[IntentionStatus]] = {
    _S.PENDING: frozenset({_S.PLANNING, _S.DROPPED, _S.INTERRUPTED}),
    _S.PLANNING: frozenset({_S.EXECUTING, _S.FAILED, _S.SUSPENDED, _S.DROPPED, _S.INTERRUPTED}),
    _S.EXECUTING: frozenset({_S.ACHIEVED, _S.FAILED, _S.SUSPENDED, _S.DROPPED, _S.INTERRUPTED}),
    _S.SUSPENDED: frozenset({_S.PLANNING, _S.DROPPED, _S.INTERRUPTED}),
}


class InvalidTransition(ValueError):
    """不允許的生命週期轉換。"""


@dataclass
class Intention:
    text: str
    priority: int = 0                                  # 數字越大越優先
    requester: str = ""                                # 提出者（使用者 / session）
    intention_id: str = field(default_factory=lambda: f"int-{uuid.uuid4().hex[:12]}")
    status: IntentionStatus = IntentionStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    attempts: int = 0                                  # 開始規劃的次數（被搶占後續做會再加一）
    reason: str = ""                                   # 最近一次狀態轉換的原因
    plan_steps: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None               # 結束時的執行摘要（ok、message、response、state_counts）
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.history:
            self.history.append({"status": self.status.value, "at": self.created_at, "reason": "submitted"})

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def transition(self, status: IntentionStatus, reason: str = "") -> None:
        if status not in _ALLOWED.get(self.status, frozenset()):
            raise InvalidTransition(f"{self.intention_id}: {self.status.value} -> {status.value}")
        if status == IntentionStatus.PLANNING:
            self.attempts += 1
        self.status = status
        self.reason = reason
        self.updated_at = time.time()
        self.history.append({"status": status.value, "at": self.updated_at, "reason": reason})

    def to_dict(self, *, with_history: bool = True) -> dict[str, Any]:
        data = {
            "intention_id": self.intention_id,
            "text": self.text,
            "priority": self.priority,
            "requester": self.requester,
            "status": self.status.value,
            "reason": self.reason,
            "attempts": self.attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "plan_steps": list(self.plan_steps),
            "result": self.result,
        }
        if with_history:
            data["history"] = list(self.history)
        return data

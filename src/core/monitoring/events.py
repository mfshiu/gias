"""
監測子系統共用資料結構。

刻意保持純資料（無 IO、無 LLM、無外部相依），方便單元測試。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeState(str, Enum):
    """atomic 節點的生命週期狀態。

    繼承 str 讓 dict 序列化時直接得到字串，便於 log / debug 輸出。
    """

    PENDING = "pending"          # 尚未派工
    IN_FLIGHT = "in_flight"      # 已派工、等待 *.result
    DONE = "done"                # 成功完成
    FAILED = "failed"            # executor 回 ok=false，或本地錯誤
    CANCELLED = "cancelled"      # 收到 *.result 且 cancelled=true
    OBSOLETE = "obsolete"        # replan 後該節點被淘汰，但結果仍可能抵達
    SKIPPED = "skipped"          # 父節點 replan 後跳過


# 終態：cursor 認為不會再進入其它狀態
TERMINAL_STATES = frozenset({
    NodeState.DONE,
    NodeState.FAILED,
    NodeState.CANCELLED,
    NodeState.OBSOLETE,
    NodeState.SKIPPED,
})


class TriggerKind(str, Enum):
    """ReplanTrigger 對 cursor 的處置建議。"""

    NONE = "none"                          # 不動，繼續執行
    RETRY_NODE = "retry_node"              # 同節點再 dispatch（保留 task_id 或換新）
    REPAIR_NODE = "repair_node"            # 改寫 params 後重派
    REPLAN_SUBTREE = "replan_subtree"      # 對指定 composite 子樹重生
    REPLAN_ROOT = "replan_root"            # 整個 plan 重生
    ABORT = "abort"                        # 放棄整個 plan


@dataclass(frozen=True, slots=True)
class ReplanDecision:
    """trigger 的決策結果。

    `affected_node_ids` 指出要操作的節點 id（cursor 中的 atomic / composite）。
    `new_params` 僅在 REPAIR_NODE 時使用。
    `subtree_root_id` 僅在 REPLAN_SUBTREE 時使用，指向要重生的 composite id。
    """

    kind: TriggerKind
    reason: str = ""
    affected_node_ids: tuple[str, ...] = ()
    new_params: dict[str, Any] | None = None
    subtree_root_id: str | None = None

    @classmethod
    def none(cls) -> "ReplanDecision":
        return cls(kind=TriggerKind.NONE)

    @classmethod
    def abort(cls, reason: str) -> "ReplanDecision":
        return cls(kind=TriggerKind.ABORT, reason=reason)

    def is_actionable(self) -> bool:
        return self.kind not in (TriggerKind.NONE,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "affected_node_ids": list(self.affected_node_ids),
            "new_params": dict(self.new_params) if self.new_params else None,
            "subtree_root_id": self.subtree_root_id,
        }


@dataclass(slots=True)
class EnvChange:
    """來自 BlackboardAgent 的環境變更通知，已轉成內部統一格式。"""

    topic: str
    action: str                             # "create" / "update" / "delete"
    new_value: Any = None
    old_value: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)

    @classmethod
    def from_blackboard_event_dict(cls, data: dict[str, Any]) -> "EnvChange":
        """從 BlackboardEvent.to_dict() 的格式建構。容錯處理缺欄位。"""
        return cls(
            topic=str(data.get("topic", "")),
            action=str(data.get("action", "update")),
            new_value=data.get("new_value"),
            old_value=data.get("old_value"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class ActionResult:
    """來自 InfoAgent / NavigationAgent 的 atomic 動作結果。"""

    task_id: str
    node_id: str = ""           # plan 節點 id（IA 在 dispatch 時記錄）
    ok: bool = False
    task: str = ""
    message: str = ""
    result: dict[str, Any] | None = None
    cancelled: bool = False
    error: str | None = None
    received_at: float = field(default_factory=time.time)

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, node_id: str = "") -> "ActionResult":
        """從 executor 回傳的 dict 建構。容錯處理缺欄位。"""
        if not isinstance(payload, dict):
            payload = {}
        result_obj = payload.get("result")
        if not isinstance(result_obj, dict) and result_obj is not None:
            result_obj = {"value": result_obj}
        return cls(
            task_id=str(payload.get("task_id", "")),
            node_id=node_id or str(payload.get("node_id", "")),
            ok=bool(payload.get("ok", False)),
            task=str(payload.get("task", "")),
            message=str(payload.get("message", "")),
            result=result_obj,
            cancelled=bool(payload.get("cancelled", False)),
            error=payload.get("error"),
        )

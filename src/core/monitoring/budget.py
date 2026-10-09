"""
BudgetGuard：限制 replan 與 retry 次數、總 deadline。

純資料 + 計數器，無 IO，方便單元測試。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Budget:
    """執行預算上限。"""

    max_replans: int = 3                        # 整個 plan 最多 replan 幾次（含 subtree + root）
    max_retries_per_node: int = 1               # 每個 atomic 失敗後可再重派幾次
    deadline_sec: float | None = None           # 整體執行 deadline；None 表示無上限
    poll_interval_sec: float = 0.5              # 監測迴圈 idle 時的等待週期
    cancel_grace_sec: float = 3.0               # 送出 cancel 後等多久仍未 ack 就放棄等待
    node_timeout_sec: float | None = 30.0       # atomic 派工後多久沒回報視為失敗（節點 deadline_sec 優先）；None 表示不限


class BudgetGuard:
    """追蹤目前已用掉多少 replan / retry，與是否超過 deadline。"""

    def __init__(self, budget: Budget | None = None):
        self.budget = budget or Budget()
        self.started_at: float = time.time()
        self.replans_used: int = 0
        self.retries_by_node: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Replan
    # ------------------------------------------------------------------
    def replan_available(self) -> bool:
        return self.replans_used < self.budget.max_replans

    def consume_replan(self) -> bool:
        if not self.replan_available():
            return False
        self.replans_used += 1
        return True

    # ------------------------------------------------------------------
    # Retry per node
    # ------------------------------------------------------------------
    def retries_left(self, node_id: str) -> int:
        used = self.retries_by_node.get(node_id, 0)
        return max(0, self.budget.max_retries_per_node - used)

    def retry_available(self, node_id: str) -> bool:
        return self.retries_left(node_id) > 0

    def consume_retry(self, node_id: str) -> bool:
        if not self.retry_available(node_id):
            return False
        self.retries_by_node[node_id] = self.retries_by_node.get(node_id, 0) + 1
        return True

    # ------------------------------------------------------------------
    # Deadline
    # ------------------------------------------------------------------
    def elapsed_sec(self) -> float:
        return time.time() - self.started_at

    def deadline_reached(self) -> bool:
        if self.budget.deadline_sec is None:
            return False
        return self.elapsed_sec() >= self.budget.deadline_sec

    def exhausted(self) -> bool:
        """Replan 配額用完 + deadline 過了，視為整體預算耗盡。

        注意：deadline 單獨到達也算耗盡（即使還有 retry 配額）。
        """
        return self.deadline_reached()

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "replans_used": self.replans_used,
            "max_replans": self.budget.max_replans,
            "max_retries_per_node": self.budget.max_retries_per_node,
            "retries_by_node": dict(self.retries_by_node),
            "elapsed_sec": round(self.elapsed_sec(), 3),
            "deadline_sec": self.budget.deadline_sec,
            "deadline_reached": self.deadline_reached(),
            "node_timeout_sec": self.budget.node_timeout_sec,
        }

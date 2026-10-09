"""
GIAS monitoring & replanning subsystem.

提供 IntentionalAgent 監測執行進度、偵測環境變動、
並依規則 + LLM 雙閘決策進行 repair / replan 的工具集。

各模組職責：
- events  : 共用資料結構（NodeState、TriggerKind、ReplanDecision、ActionResult、EnvChange）
- cursor  : PlanCursor：追蹤 plan 樹中每個 atomic 節點的生命週期，支援拓撲分層
- budget  : BudgetGuard：限制 replan / retry / deadline，避免無限迴圈
- monitor : ExecutionMonitor：把黑板事件與 executor 回傳結果聚合到單一 queue
- trigger : ReplanTrigger：規則優先 + LLM 升級的再思考決策
- repair  : PlanRepair：套用決策（retry / repair_node / replan_subtree / replan_root）
"""

from .events import (
    NodeState,
    TriggerKind,
    ReplanDecision,
    EnvChange,
    ActionResult,
)
from .cursor import NodeRecord, PlanCursor
from .budget import Budget, BudgetGuard
from .monitor import ExecutionMonitor
from .trigger import ReplanTrigger
from .repair import PlanRepair

__all__ = [
    "NodeState",
    "TriggerKind",
    "ReplanDecision",
    "EnvChange",
    "ActionResult",
    "NodeRecord",
    "PlanCursor",
    "Budget",
    "BudgetGuard",
    "ExecutionMonitor",
    "ReplanTrigger",
    "PlanRepair",
]

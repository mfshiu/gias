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
- llm_advisor : LLMReplanAdvisor：規則判斷不了時的 LLM 判斷器（注入 ReplanTrigger）
- entity_match : EntityMatcher：判斷計畫步驟是否提到環境事件的實體（完整名稱、別名、從屬）
"""

from .events import (
    NodeState,
    TriggerKind,
    ReplanDecision,
    EnvChange,
    ActionResult,
    ExecutorAdaptation,
    ProgressUpdate,
)
from .cursor import DuplicateNodeIdError, NodeRecord, PlanCursor, find_dependency_cycles, find_duplicate_node_ids
from .budget import Budget, BudgetGuard
from .monitor import ExecutionMonitor
from .trigger import ReplanTrigger
from .repair import PlanRepair
from .llm_advisor import LLMReplanAdvisor
from .entity_match import EntityMatcher

__all__ = [
    "NodeState",
    "TriggerKind",
    "ReplanDecision",
    "EnvChange",
    "ActionResult",
    "ExecutorAdaptation",
    "ProgressUpdate",
    "DuplicateNodeIdError",
    "NodeRecord",
    "PlanCursor",
    "find_dependency_cycles",
    "find_duplicate_node_ids",
    "Budget",
    "BudgetGuard",
    "ExecutionMonitor",
    "ReplanTrigger",
    "PlanRepair",
    "LLMReplanAdvisor",
    "EntityMatcher",
]

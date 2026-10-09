"""
ReplanTrigger：基於 ActionResult / EnvChange 與 cursor 狀態決定如何 replan。

策略：
1. 規則第一（廉價、可預測）
2. LLM 輔助（enable_llm_assist，預設關閉）：
   - 節點失敗時先問 LLM，可依錯誤內容改參數重派（REPAIR_NODE）等
   - 規則對應不到節點的相關環境變動，交給 LLM 判斷是否影響計畫
   - LLM 失敗、回 NONE 或超出預算時一律回到規則
3. 預設保守：拿不準時回 NONE，由迴圈下一回合再評估

本模組純資料運算 + 可選 LLM 呼叫；不直接動 plan 樹。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .events import (
    ActionResult,
    EnvChange,
    NodeState,
    ReplanDecision,
    TriggerKind,
)
from .cursor import PlanCursor


@dataclass(slots=True)
class TriggerConfig:
    """觸發策略的可調參數。"""

    enable_retry: bool = True
    enable_subtree_replan: bool = True
    enable_root_replan: bool = True
    enable_llm_assist: bool = False
    abort_after_failures: int = 5             # 一個 plan 內累積失敗達此值即 ABORT
    abort_after_obsolete: int = 8             # 累積 OBSOLETE 達此值即 ABORT
    relevant_topic_prefixes: tuple[str, ...] = ("Zone/", "Booth/")
    llm_max_calls: int = 10                   # 一個 plan 最多諮詢 LLM 幾次（控制成本與延遲）


class ReplanTrigger:
    """規則 + LLM 雙閘決策器。"""

    def __init__(
        self,
        *,
        config: TriggerConfig | None = None,
        budget_guard=None,                     # BudgetGuard
        llm: Any | None = None,
        logger: logging.Logger | None = None,
        llm_decider: Callable[..., ReplanDecision] | None = None,
    ):
        self.config = config or TriggerConfig()
        self.budget_guard = budget_guard
        self.llm = llm
        self.logger = logger or logging.getLogger(__name__)
        # LLM 判斷器（預設由 IntentionalAgent 注入 LLMReplanAdvisor），
        # 簽名 (*, intent, env_changes, action_results, cursor) -> ReplanDecision
        self._llm_decider = llm_decider
        self._llm_calls = 0
        self._llm_cap_logged = False

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def decide(
        self,
        *,
        action_results: list[ActionResult],
        env_changes: list[EnvChange],
        cursor: PlanCursor,
        intent: str = "",
    ) -> ReplanDecision:
        # 1) 預算用盡 → ABORT
        if self.budget_guard is not None and self.budget_guard.deadline_reached():
            return ReplanDecision.abort("deadline reached")

        # 2) 失敗累積過多 → ABORT
        failed_count = sum(
            1 for r in cursor.all_records() if r.state == NodeState.FAILED
        )
        if failed_count >= self.config.abort_after_failures:
            return ReplanDecision.abort(
                f"too many failures: {failed_count}>={self.config.abort_after_failures}"
            )
        obsolete_count = sum(
            1 for r in cursor.all_records() if r.state == NodeState.OBSOLETE
        )
        if obsolete_count >= self.config.abort_after_obsolete:
            return ReplanDecision.abort(
                f"too many obsolete: {obsolete_count}>={self.config.abort_after_obsolete}"
            )

        # 3) 動作結果優先決策
        decision = self._decide_from_results(action_results, cursor, intent=intent)
        if decision.is_actionable():
            return decision

        # 4) 環境變動觸發
        decision = self._decide_from_env(env_changes, cursor)
        if decision.is_actionable():
            return decision

        # 5) LLM 升級（可選）：規則對應不到節點的相關環境變動（例：名稱寫法不同）
        relevant = self._relevant_changes(env_changes)
        if relevant and _has_unfinished(cursor):
            decision = self._consult_llm(intent=intent, env_changes=relevant, action_results=[], cursor=cursor)
            if decision is not None:
                return decision

        return ReplanDecision.none()

    # ------------------------------------------------------------------
    # LLM 升級
    # ------------------------------------------------------------------
    def _consult_llm(
        self,
        *,
        intent: str,
        env_changes: list[EnvChange],
        action_results: list[ActionResult],
        cursor: PlanCursor,
    ) -> ReplanDecision | None:
        """詢問 LLM；回傳可執行且在預算內的決策，否則回傳 None（由規則接手）。"""
        if not self.config.enable_llm_assist or self._llm_decider is None:
            return None
        if self._llm_calls >= self.config.llm_max_calls:
            if not self._llm_cap_logged:
                self.logger.info("LLM trigger assist skipped: llm_max_calls=%d reached", self.config.llm_max_calls)
                self._llm_cap_logged = True
            return None
        self._llm_calls += 1
        try:
            decision = self._llm_decider(
                intent=intent,
                env_changes=env_changes,
                action_results=action_results,
                cursor=cursor,
            )
        except Exception as e:
            self.logger.warning("LLM trigger assist failed: %s", e)
            return None
        if not isinstance(decision, ReplanDecision) or not decision.is_actionable():
            return None
        if not self._within_budget(decision, cursor):
            self.logger.info("LLM decision %s exceeds budget; falling back to rules", decision.kind.value)
            return None
        return decision

    def _within_budget(self, decision: ReplanDecision, cursor: PlanCursor) -> bool:
        """LLM 的決策若超出預算會被 PlanRepair 拒絕而卡住節點，因此先在這裡擋下。"""
        guard = self.budget_guard
        if guard is None:
            return True
        if decision.kind in (TriggerKind.RETRY_NODE, TriggerKind.REPAIR_NODE):
            for nid in decision.affected_node_ids:
                rec = cursor.record(nid)
                pending_repair = (
                    decision.kind == TriggerKind.REPAIR_NODE
                    and rec is not None
                    and rec.state == NodeState.PENDING
                )
                if not pending_repair and not guard.retry_available(nid):
                    return False
            return True
        if decision.kind in (TriggerKind.REPLAN_SUBTREE, TriggerKind.REPLAN_ROOT):
            return guard.replan_available()
        return True

    def _relevant_changes(self, changes: list[EnvChange]) -> list[EnvChange]:
        return [
            c for c in changes
            if any(c.topic.startswith(p) for p in self.config.relevant_topic_prefixes)
        ]

    # ------------------------------------------------------------------
    # Rule: action result
    # ------------------------------------------------------------------
    def _decide_from_results(
        self, results: list[ActionResult], cursor: PlanCursor, *, intent: str = ""
    ) -> ReplanDecision:
        for ar in results:
            rec = cursor.find_by_task_id(ar.task_id)
            if rec is None:
                continue
            if rec.state == NodeState.DONE:
                continue
            if rec.state == NodeState.FAILED:
                # LLM 可依錯誤內容選擇更合適的處置（例：改參數重派 REPAIR_NODE）；
                # 未啟用、失敗或超出預算時沿用下方規則
                decision = self._consult_llm(intent=intent, env_changes=[], action_results=[ar], cursor=cursor)
                if decision is not None:
                    return decision
                # 預算允許 retry
                if self.config.enable_retry and self.budget_guard is not None and self.budget_guard.retry_available(rec.node_id):
                    return ReplanDecision(
                        kind=TriggerKind.RETRY_NODE,
                        reason=f"node {rec.node_id} failed; retry available",
                        affected_node_ids=(rec.node_id,),
                    )
                # 沒 retry 配額 → 升級為 subtree replan
                if self.config.enable_subtree_replan:
                    parent_id = rec.parent_id or _root_id(cursor)
                    return ReplanDecision(
                        kind=TriggerKind.REPLAN_SUBTREE,
                        reason=f"node {rec.node_id} failed; retries exhausted",
                        affected_node_ids=(rec.node_id,),
                        subtree_root_id=parent_id,
                    )
                # 都不能 → ABORT
                return ReplanDecision.abort(
                    f"node {rec.node_id} failed and no recovery strategy"
                )
        return ReplanDecision.none()

    # ------------------------------------------------------------------
    # Rule: env change
    # ------------------------------------------------------------------
    def _decide_from_env(
        self, changes: list[EnvChange], cursor: PlanCursor
    ) -> ReplanDecision:
        if not changes:
            return ReplanDecision.none()
        # 過濾相關 topic
        relevant = self._relevant_changes(changes)
        if not relevant:
            return ReplanDecision.none()

        # 規則：若任何 PENDING 或 IN_FLIGHT 節點的 params/intent 命中變更的 zone/booth → subtree replan
        affected = self._find_affected_nodes(relevant, cursor)
        if not affected:
            return ReplanDecision.none()

        # 多筆影響或跨 composite → root replan
        if self.config.enable_root_replan and self._spans_multiple_composites(affected, cursor):
            return ReplanDecision(
                kind=TriggerKind.REPLAN_ROOT,
                reason=f"env changes affect multiple composites: {[r.node_id for r in affected]}",
                affected_node_ids=tuple(r.node_id for r in affected),
            )

        if self.config.enable_subtree_replan:
            first = affected[0]
            parent_id = first.parent_id or _root_id(cursor)
            return ReplanDecision(
                kind=TriggerKind.REPLAN_SUBTREE,
                reason=f"env change affects node {first.node_id} via {[c.topic for c in relevant]}",
                affected_node_ids=tuple(r.node_id for r in affected),
                subtree_root_id=parent_id,
            )

        return ReplanDecision.none()

    def _find_affected_nodes(self, changes: list[EnvChange], cursor: PlanCursor) -> list:
        affected = []
        # 抓出變動裡涉及的 zone / booth 名稱
        keywords = set()
        for c in changes:
            # topic 結構：Zone/<name>/<prop> 或 Booth/<id>/<prop>
            parts = c.topic.split("/")
            if len(parts) >= 2 and parts[1]:
                keywords.add(parts[1])
            # 也吃 metadata 中的線索；BlackboardWatcher 的節點事件帶 node_id，
            # 關係事件（如 Zone/CURRENT_STATE/State）帶 source_id
            md = c.metadata or {}
            for key in ("zone", "booth_id", "zone_name", "target", "node_id", "source_id"):
                v = md.get(key)
                if isinstance(v, str) and v:
                    keywords.add(v)
        if not keywords:
            return affected
        for rec in cursor.all_records():
            if rec.state not in (NodeState.PENDING, NodeState.IN_FLIGHT):
                continue
            blob = self._node_search_blob(rec.node)
            for kw in keywords:
                if kw and kw in blob:
                    affected.append(rec)
                    break
        return affected

    @staticmethod
    def _node_search_blob(node: dict[str, Any]) -> str:
        parts = [str(node.get("intent", "")), str(node.get("task", ""))]
        params = node.get("params") or {}
        if isinstance(params, dict):
            parts.extend(str(v) for v in params.values())
        return " ".join(parts)

    def _spans_multiple_composites(self, recs: list, cursor: PlanCursor) -> bool:
        parents = {r.parent_id for r in recs}
        return len(parents) > 1


def _has_unfinished(cursor: PlanCursor) -> bool:
    return any(r.state in (NodeState.PENDING, NodeState.IN_FLIGHT) for r in cursor.all_records())


def _root_id(cursor: PlanCursor) -> str:
    root = cursor.root()
    return str(root.get("id", "root"))

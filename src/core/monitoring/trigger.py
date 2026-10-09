"""
ReplanTrigger：基於 ActionResult / EnvChange 與 cursor 狀態決定如何 replan。

策略：
1. 規則第一（廉價、可預測）
2. LLM 第二（規則不明時升級；可關閉）
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
        # 可選注入：自訂 LLM 判斷器（測試用），簽名 (intent, available_actions, env_changes) -> ReplanDecision
        self._llm_decider = llm_decider

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
        decision = self._decide_from_results(action_results, cursor)
        if decision.is_actionable():
            return decision

        # 4) 環境變動觸發
        decision = self._decide_from_env(env_changes, cursor)
        if decision.is_actionable():
            return decision

        # 5) LLM 升級（可選）
        if (
            self.config.enable_llm_assist
            and (action_results or env_changes)
            and self._llm_decider is not None
        ):
            try:
                decision = self._llm_decider(
                    intent=intent,
                    env_changes=env_changes,
                    action_results=action_results,
                    cursor=cursor,
                )
                if isinstance(decision, ReplanDecision):
                    return decision
            except Exception as e:  # pragma: no cover
                self.logger.warning("LLM trigger assist failed: %s", e)

        return ReplanDecision.none()

    # ------------------------------------------------------------------
    # Rule: action result
    # ------------------------------------------------------------------
    def _decide_from_results(
        self, results: list[ActionResult], cursor: PlanCursor
    ) -> ReplanDecision:
        for ar in results:
            rec = cursor.find_by_task_id(ar.task_id)
            if rec is None:
                continue
            if rec.state == NodeState.DONE:
                continue
            if rec.state == NodeState.FAILED:
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
        relevant = [
            c for c in changes
            if any(c.topic.startswith(p) for p in self.config.relevant_topic_prefixes)
        ]
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
            # 也吃 metadata 中的線索
            md = c.metadata or {}
            for key in ("zone", "booth_id", "zone_name", "target"):
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


def _root_id(cursor: PlanCursor) -> str:
    root = cursor.root()
    return str(root.get("id", "root"))

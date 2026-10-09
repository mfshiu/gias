"""
LLMReplanAdvisor：ReplanTrigger 的 LLM 升級判斷器。

規則能處理的情況（失敗重試、環境事件直接命中節點）由 ReplanTrigger 自己處理；
本模組負責規則判斷不了或需要語意理解的情況：
- 節點失敗時，依錯誤內容選擇處置（例：參數錯誤 → REPAIR_NODE 改參數重派）
- 環境變動與節點的關聯無法用字面比對（例：區域 ID 與中文名稱寫法不同）

LLM 的回覆一律經過驗證（節點 id 必須存在、狀態必須允許該操作），
不合格時回傳 NONE，由 ReplanTrigger 回到規則。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .cursor import PlanCursor
from .events import ActionResult, EnvChange, NodeState, ReplanDecision, TriggerKind


_KINDS = {
    "none": TriggerKind.NONE,
    "retry_node": TriggerKind.RETRY_NODE,
    "repair_node": TriggerKind.REPAIR_NODE,
    "replan_subtree": TriggerKind.REPLAN_SUBTREE,
    "replan_root": TriggerKind.REPLAN_ROOT,
    "abort": TriggerKind.ABORT,
}

# 各決策允許作用的節點狀態（IN_FLIGHT 會先由 IntentionalAgent 送 cancel）
_RETRY_STATES = frozenset({NodeState.FAILED, NodeState.CANCELLED, NodeState.IN_FLIGHT})
_REPAIR_STATES = _RETRY_STATES | {NodeState.PENDING}

_SYSTEM_PROMPT = """You are the execution monitor of an intention-driven agent.
A hierarchical plan is being executed step by step. Decide whether the new events require changing the plan.
Prefer the least disruptive option that still achieves the user's intent.

Options:
- none: the events do not affect the remaining steps
- retry_node: re-run the listed failed/cancelled steps unchanged (transient failure)
- repair_node: re-run the listed steps (or update them if still pending) with corrected arguments in new_params
- replan_subtree: regenerate the composite step subtree_root_id because its remaining steps can no longer achieve its goal
- replan_root: regenerate the whole plan
- abort: the intent can no longer be achieved

Rules:
- Only refer to step ids that appear in plan_steps.
- Completed steps (state "done") cannot be changed.
- Environment changes can only affect steps whose state is "pending" or "in_flight".
- new_params keys must be existing parameter names of that step.

Return ONLY a JSON object:
{"decision": "none|retry_node|repair_node|replan_subtree|replan_root|abort",
 "node_ids": ["step id", ...], "new_params": {}, "subtree_root_id": "", "reason": "short explanation"}"""


class LLMReplanAdvisor:
    """把監測事件交給 LLM 判斷，回傳經過驗證的 ReplanDecision。

    可直接作為 ReplanTrigger 的 llm_decider。
    """

    def __init__(
        self,
        llm: Any,
        *,
        logger: logging.Logger | None = None,
        max_steps: int = 40,
        max_env_changes: int = 20,
    ):
        self.llm = llm
        self.logger = logger or logging.getLogger(__name__)
        self.max_steps = max_steps
        self.max_env_changes = max_env_changes

    def __call__(
        self,
        *,
        intent: str,
        env_changes: list[EnvChange],
        action_results: list[ActionResult],
        cursor: PlanCursor,
    ) -> ReplanDecision:
        messages = self.build_messages(
            intent=intent, env_changes=env_changes, action_results=action_results, cursor=cursor
        )
        reply = self.llm.json(messages, schema=None)
        decision = self.to_decision(reply, cursor)
        self.logger.info("LLM replan advice: %s", decision.to_dict())
        return decision

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------
    def build_messages(
        self,
        *,
        intent: str,
        env_changes: list[EnvChange],
        action_results: list[ActionResult],
        cursor: PlanCursor,
    ) -> list[dict[str, str]]:
        steps = [
            {
                "id": r.node_id,
                "parent_id": r.parent_id,
                "intent": r.node.get("intent", ""),
                "task": r.node.get("task"),
                "params": r.node.get("params") or {},
                "state": r.state.value,
                "error": r.error,
            }
            for r in cursor.all_records()
            if r.state not in (NodeState.OBSOLETE, NodeState.SKIPPED)
        ][: self.max_steps]
        failed = []
        for ar in action_results:
            rec = cursor.find_by_task_id(ar.task_id)
            failed.append({
                "step_id": rec.node_id if rec is not None else ar.node_id,
                "task": ar.task, "error": ar.error, "message": ar.message,
            })
        payload = {
            "intent": intent,
            "plan_steps": steps,
            "failed_results": failed,
            "environment_changes": [
                {"topic": c.topic, "action": c.action, "old_value": c.old_value,
                 "new_value": c.new_value, "metadata": c.metadata}
                for c in env_changes[: self.max_env_changes]
            ],
        }
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def to_decision(self, reply: Any, cursor: PlanCursor) -> ReplanDecision:
        if not isinstance(reply, dict):
            return ReplanDecision.none()
        kind = _KINDS.get(str(reply.get("decision", "")).strip().lower())
        if kind is None or kind == TriggerKind.NONE:
            return ReplanDecision.none()
        reason = f"llm: {str(reply.get('reason', '')).strip()}"
        node_ids = [str(n) for n in (reply.get("node_ids") or []) if cursor.record(str(n)) is not None]

        if kind in (TriggerKind.RETRY_NODE, TriggerKind.REPAIR_NODE):
            allowed = _RETRY_STATES if kind == TriggerKind.RETRY_NODE else _REPAIR_STATES
            node_ids = [n for n in node_ids if cursor.record(n).state in allowed]
            if not node_ids:
                return ReplanDecision.none()
            if kind == TriggerKind.RETRY_NODE:
                return ReplanDecision(kind=kind, reason=reason, affected_node_ids=tuple(node_ids))
            new_params = reply.get("new_params")
            if not isinstance(new_params, dict) or not new_params:
                return ReplanDecision.none()
            return ReplanDecision(
                kind=kind, reason=reason, affected_node_ids=tuple(node_ids), new_params=dict(new_params)
            )

        if kind == TriggerKind.REPLAN_SUBTREE:
            root_id = self._resolve_composite_id(str(reply.get("subtree_root_id") or ""), node_ids, cursor)
            if root_id is None:
                return ReplanDecision.none()
            return ReplanDecision(
                kind=kind, reason=reason, affected_node_ids=tuple(node_ids), subtree_root_id=root_id
            )

        if kind == TriggerKind.REPLAN_ROOT:
            return ReplanDecision(kind=kind, reason=reason, affected_node_ids=tuple(node_ids))

        return ReplanDecision.abort(reason)

    @staticmethod
    def _resolve_composite_id(candidate: str, node_ids: list[str], cursor: PlanCursor) -> str | None:
        """subtree_root_id 必須是樹中的 composite；給的是 atomic 時改用其父節點。"""
        rec = cursor.record(candidate) if candidate else None
        if rec is not None:
            candidate = rec.parent_id or ""
        if candidate:
            node = cursor.composite_node(candidate)
            if isinstance(node, dict) and cursor.record(candidate) is None:
                return candidate
        for nid in node_ids:
            parent = cursor.record(nid).parent_id
            if parent:
                return parent
        return None

"""
PlanRepair：把 ReplanDecision 套用到 PlanCursor。

職責劃分：
- RETRY_NODE / REPAIR_NODE：cursor 自己就能處理（reset_for_retry + 可選 params 覆寫）
- REPLAN_SUBTREE：需要從 planner 重新生成一棵子 plan，包成新的 composite
- REPLAN_ROOT：需要 IA 提供 plan_intention callable 來重生整個 plan

為了單元測試，本模組不直接相依 IntentionalAgent / RecursivePlanner；
caller 在建構時注入 `planner_callback` / `root_planner_callback`。

重規劃時會把「重規劃脈絡」傳給 planner（見 build_replan_context）：
    {"replan": {"kind", "reason", "affected_steps": [...]}, "env_facts": {...}}
planner_callback 簽名為 (sub_intent, context)；root_planner_callback 可為 (intent) 或 (intent, context)。
"""

from __future__ import annotations

import copy
import inspect
import logging
import uuid
from typing import Any, Callable

from .events import ReplanDecision, TriggerKind
from .cursor import DuplicateNodeIdError, PlanCursor


class PlanRepair:
    """套用 ReplanDecision 到 PlanCursor。"""

    def __init__(
        self,
        *,
        budget_guard=None,                                                   # BudgetGuard
        planner_callback: Callable[[str, dict[str, Any] | None], dict[str, Any] | None] | None = None,
        root_planner_callback: Callable[..., dict[str, Any] | None] | None = None,
        env_facts_provider: Callable[[], dict[str, Any]] | None = None,
        logger: logging.Logger | None = None,
    ):
        self.budget_guard = budget_guard
        self.planner_callback = planner_callback
        self.root_planner_callback = root_planner_callback
        self.env_facts_provider = env_facts_provider
        self.logger = logger or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def apply(
        self,
        decision: ReplanDecision,
        cursor: PlanCursor,
        *,
        intent: str = "",
    ) -> dict[str, Any]:
        """套用決策。

        回傳：
            {
              "applied": bool,
              "kind": str,
              "reason": str,
              "affected": [node_id, ...],
              "details": {...},
            }
        """
        kind = decision.kind
        if kind == TriggerKind.NONE:
            return {"applied": False, "kind": kind.value, "reason": "no-op"}

        if kind == TriggerKind.ABORT:
            return {"applied": True, "kind": kind.value, "reason": decision.reason}

        if kind == TriggerKind.RETRY_NODE:
            return self._apply_retry(decision, cursor)

        if kind == TriggerKind.REPAIR_NODE:
            return self._apply_repair_node(decision, cursor)

        if kind == TriggerKind.REPLAN_SUBTREE:
            return self._apply_replan_subtree(decision, cursor, intent=intent)

        if kind == TriggerKind.REPLAN_ROOT:
            return self._apply_replan_root(decision, cursor, intent=intent)

        return {"applied": False, "kind": kind.value, "reason": "unknown kind"}

    # ------------------------------------------------------------------
    # RETRY / REPAIR
    # ------------------------------------------------------------------
    def _apply_retry(self, decision: ReplanDecision, cursor: PlanCursor) -> dict[str, Any]:
        if not decision.affected_node_ids:
            return {"applied": False, "kind": decision.kind.value, "reason": "no affected nodes"}
        applied_ids: list[str] = []
        for nid in decision.affected_node_ids:
            if self.budget_guard is not None and not self.budget_guard.consume_retry(nid):
                self.logger.info("Retry budget exhausted for node %s", nid)
                continue
            try:
                cursor.reset_for_retry(nid)
                applied_ids.append(nid)
            except Exception as e:
                self.logger.warning("Retry reset failed for %s: %s", nid, e)
        return {
            "applied": bool(applied_ids),
            "kind": decision.kind.value,
            "reason": decision.reason,
            "affected": applied_ids,
        }

    def _apply_repair_node(
        self, decision: ReplanDecision, cursor: PlanCursor
    ) -> dict[str, Any]:
        if not decision.affected_node_ids:
            return {"applied": False, "kind": decision.kind.value, "reason": "no affected nodes"}
        applied_ids: list[str] = []
        new_params = dict(decision.new_params or {})
        for nid in decision.affected_node_ids:
            # 尚未派工的節點只需改參數，不消耗 retry 配額
            if cursor.update_pending_params(nid, new_params):
                applied_ids.append(nid)
                continue
            if self.budget_guard is not None and not self.budget_guard.consume_retry(nid):
                self.logger.info("Repair retry budget exhausted for node %s", nid)
                continue
            try:
                cursor.reset_for_retry(nid, new_params=new_params)
                applied_ids.append(nid)
            except Exception as e:
                self.logger.warning("Repair reset failed for %s: %s", nid, e)
        return {
            "applied": bool(applied_ids),
            "kind": decision.kind.value,
            "reason": decision.reason,
            "affected": applied_ids,
            "details": {"new_params": new_params},
        }

    # ------------------------------------------------------------------
    # SUBTREE
    # ------------------------------------------------------------------
    def _apply_replan_subtree(
        self, decision: ReplanDecision, cursor: PlanCursor, *, intent: str
    ) -> dict[str, Any]:
        if self.budget_guard is not None and not self.budget_guard.consume_replan():
            return {"applied": False, "kind": decision.kind.value, "reason": "replan budget exhausted"}
        if self.planner_callback is None:
            return {"applied": False, "kind": decision.kind.value, "reason": "no planner_callback"}

        composite_id = decision.subtree_root_id or _affected_parent_id(decision, cursor)
        composite = cursor.composite_node(composite_id) if composite_id else None
        if composite is None:
            return {"applied": False, "kind": decision.kind.value, "reason": f"composite {composite_id} not found"}

        sub_intent = composite.get("intent") or intent
        context = self.build_replan_context(decision, cursor)
        try:
            new_subtree = self.planner_callback(sub_intent, context)
        except Exception as e:
            self.logger.warning("Subtree planner_callback failed: %s", e)
            return {"applied": False, "kind": decision.kind.value, "reason": f"planner failed: {e}"}
        if not new_subtree:
            return {"applied": False, "kind": decision.kind.value, "reason": "planner returned empty"}

        # 對 atomic id 做防衝突重編，避免新舊 id 撞到
        _renumber_ids_in_subtree(new_subtree, prefix=f"r{cursor and uuid.uuid4().hex[:4] or ''}")

        ok = cursor.replace_subtree(composite_id, new_subtree)
        if not ok:
            self.logger.warning("Subtree replan rejected: node ids clash under %s", composite_id)
        return {
            "applied": ok,
            "kind": decision.kind.value,
            "reason": decision.reason if ok else "replace_subtree rejected: node id clash",
            "affected": list(decision.affected_node_ids),
            "details": {"composite_id": composite_id, "sub_intent": sub_intent},
        }

    # ------------------------------------------------------------------
    # ROOT
    # ------------------------------------------------------------------
    def _apply_replan_root(
        self, decision: ReplanDecision, cursor: PlanCursor, *, intent: str
    ) -> dict[str, Any]:
        if self.budget_guard is not None and not self.budget_guard.consume_replan():
            return {"applied": False, "kind": decision.kind.value, "reason": "replan budget exhausted"}
        if self.root_planner_callback is None:
            return {"applied": False, "kind": decision.kind.value, "reason": "no root_planner_callback"}
        try:
            if _accepts_context(self.root_planner_callback):
                new_plan = self.root_planner_callback(intent, self.build_replan_context(decision, cursor))
            else:
                new_plan = self.root_planner_callback(intent)
        except Exception as e:
            self.logger.warning("Root planner_callback failed: %s", e)
            return {"applied": False, "kind": decision.kind.value, "reason": f"planner failed: {e}"}
        if not new_plan:
            return {"applied": False, "kind": decision.kind.value, "reason": "planner returned empty"}
        _renumber_ids_in_subtree(new_plan, prefix=f"R{uuid.uuid4().hex[:4]}")
        try:
            cursor.replace_root(new_plan)
        except DuplicateNodeIdError as e:
            self.logger.warning("Root replan rejected: %s", e)
            return {"applied": False, "kind": decision.kind.value, "reason": f"invalid plan: {e}"}
        return {
            "applied": True,
            "kind": decision.kind.value,
            "reason": decision.reason,
            "affected": list(decision.affected_node_ids),
        }


    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------
    def build_replan_context(self, decision: ReplanDecision, cursor: PlanCursor) -> dict[str, Any]:
        """整理給 planner 的重規劃脈絡：為何重規劃、哪些步驟出了問題、目前環境事實。

        沒有新資訊的重規劃，LLM 多半會產出與原本相同的計畫。
        """
        affected: list[dict[str, Any]] = []
        for nid in decision.affected_node_ids:
            rec = cursor.record(nid)
            if rec is None:
                continue
            affected.append({
                "id": nid,
                "intent": rec.node.get("intent", ""),
                "task": rec.node.get("task"),
                "params": rec.node.get("params") or {},
                "state": rec.state.value,
                "error": rec.error,
            })
        context: dict[str, Any] = {
            "replan": {"kind": decision.kind.value, "reason": decision.reason, "affected_steps": affected},
        }
        if self.env_facts_provider is not None:
            try:
                facts = self.env_facts_provider()
            except Exception as e:
                self.logger.warning("env_facts_provider failed: %s", e)
                facts = None
            if facts:
                context["env_facts"] = facts
        return context


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _accepts_context(callback: Callable[..., Any]) -> bool:
    """root_planner_callback 是否接受第二個參數（向後相容只收 intent 的 callable）。"""
    try:
        params = list(inspect.signature(callback).parameters.values())
    except (TypeError, ValueError):
        return False
    if any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params):
        return True
    positional = [p for p in params if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    return len(positional) >= 2


def _affected_parent_id(decision: ReplanDecision, cursor: PlanCursor) -> str | None:
    for nid in decision.affected_node_ids:
        rec = cursor.record(nid)
        if rec and rec.parent_id:
            return rec.parent_id
    return None


def _renumber_ids_in_subtree(node: dict[str, Any], *, prefix: str) -> None:
    """為 LLM 重生的子樹節點加上唯一 prefix，避免與 cursor 既有 id 衝突。"""
    if not isinstance(node, dict):
        return
    nid = str(node.get("id") or "")
    if nid and not nid.startswith(prefix):
        node["id"] = f"{prefix}-{nid}"
    elif not nid:
        node["id"] = f"{prefix}-{uuid.uuid4().hex[:6]}"
    # 同步 execution_logic 內的 from_id / to_id
    el = node.get("execution_logic")
    if isinstance(el, list):
        for rel in el:
            if not isinstance(rel, dict):
                continue
            for k in ("from_id", "to_id"):
                v = rel.get(k)
                if isinstance(v, str) and v and not v.startswith(prefix):
                    rel[k] = f"{prefix}-{v}"
    for child in node.get("sub_plans") or []:
        _renumber_ids_in_subtree(child, prefix=prefix)

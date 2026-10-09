"""PlanRepair 單元測試。"""

from __future__ import annotations

from src.core.monitoring.budget import Budget, BudgetGuard
from src.core.monitoring.cursor import PlanCursor
from src.core.monitoring.events import (
    NodeState,
    ReplanDecision,
    TriggerKind,
)
from src.core.monitoring.repair import PlanRepair


def _plan_two_branches() -> dict:
    return {
        "id": "root",
        "intent": "去 AI 區並參觀展品",
        "type": "composite",
        "sub_plans": [
            {
                "id": "L2-1",
                "intent": "去 AI 區",
                "type": "composite",
                "sub_plans": [
                    {"id": "a", "type": "atomic", "is_atomic": True, "task": "LocateExhibit",
                     "params": {"target_name": "AI"}, "sub_plans": []},
                ],
                "execution_logic": [],
            },
            {
                "id": "L2-2",
                "intent": "介紹展品",
                "type": "composite",
                "sub_plans": [
                    {"id": "b", "type": "atomic", "is_atomic": True, "task": "ExplainExhibit",
                     "params": {"target_name": "X"}, "sub_plans": []},
                ],
                "execution_logic": [],
            },
        ],
        "execution_logic": [{"type": "Sequence", "from_id": "L2-1", "to_id": "L2-2"}],
    }


def test_apply_none_is_noop():
    c = PlanCursor(_plan_two_branches())
    repair = PlanRepair(budget_guard=BudgetGuard())
    out = repair.apply(ReplanDecision.none(), c)
    assert out["applied"] is False


def test_apply_retry_resets_state():
    c = PlanCursor(_plan_two_branches())
    c.mark_dispatched("a"); c.mark_failed("a", "boom")
    g = BudgetGuard(Budget(max_retries_per_node=1))
    repair = PlanRepair(budget_guard=g)
    decision = ReplanDecision(kind=TriggerKind.RETRY_NODE, affected_node_ids=("a",))
    out = repair.apply(decision, c)
    assert out["applied"] is True
    assert c.record("a").state == NodeState.PENDING
    assert g.retries_by_node["a"] == 1


def test_apply_retry_budget_exhausted():
    c = PlanCursor(_plan_two_branches())
    c.mark_dispatched("a"); c.mark_failed("a", "boom")
    g = BudgetGuard(Budget(max_retries_per_node=0))
    repair = PlanRepair(budget_guard=g)
    decision = ReplanDecision(kind=TriggerKind.RETRY_NODE, affected_node_ids=("a",))
    out = repair.apply(decision, c)
    assert out["applied"] is False
    assert c.record("a").state == NodeState.FAILED


def test_apply_repair_node_overrides_params():
    c = PlanCursor(_plan_two_branches())
    c.mark_dispatched("a"); c.mark_failed("a", "boom")
    g = BudgetGuard(Budget(max_retries_per_node=1))
    repair = PlanRepair(budget_guard=g)
    decision = ReplanDecision(
        kind=TriggerKind.REPAIR_NODE,
        affected_node_ids=("a",),
        new_params={"target_name": "Robotics"},
    )
    out = repair.apply(decision, c)
    assert out["applied"] is True
    rec = c.record("a")
    assert rec.state == NodeState.PENDING
    assert rec.node["params"]["target_name"] == "Robotics"


def test_apply_replan_subtree_calls_planner_and_replaces():
    c = PlanCursor(_plan_two_branches())
    captured = {}

    def fake_planner(sub_intent, env_facts):
        captured["sub_intent"] = sub_intent
        return {
            "id": "L2-1-NEW",
            "intent": sub_intent,
            "type": "composite",
            "sub_plans": [
                {"id": "newa", "type": "atomic", "is_atomic": True, "task": "SuggestRoute",
                 "params": {}, "sub_plans": []},
            ],
            "execution_logic": [],
        }

    g = BudgetGuard()
    repair = PlanRepair(
        budget_guard=g,
        planner_callback=fake_planner,
    )
    decision = ReplanDecision(
        kind=TriggerKind.REPLAN_SUBTREE,
        affected_node_ids=("a",),
        subtree_root_id="L2-1",
    )
    out = repair.apply(decision, c)
    assert out["applied"] is True
    assert captured["sub_intent"] == "去 AI 區"
    assert g.replans_used == 1
    # 原 a 被 SKIPPED；新節點存在
    assert c.record("a").state == NodeState.SKIPPED
    # 新節點 id 經過 prefix 重編；可從 next_ready 看見
    ready_ids = [r.node_id for r in c.next_ready_atomics()]
    assert any(rid.endswith("newa") for rid in ready_ids), ready_ids


def test_apply_replan_subtree_budget_exhausted():
    c = PlanCursor(_plan_two_branches())
    g = BudgetGuard(Budget(max_replans=0))
    repair = PlanRepair(budget_guard=g, planner_callback=lambda *_: {"id": "x", "sub_plans": []})
    out = repair.apply(
        ReplanDecision(kind=TriggerKind.REPLAN_SUBTREE, subtree_root_id="L2-1"),
        c,
    )
    assert out["applied"] is False


def test_apply_replan_subtree_planner_returns_empty():
    c = PlanCursor(_plan_two_branches())
    g = BudgetGuard()
    repair = PlanRepair(budget_guard=g, planner_callback=lambda *_: None)
    out = repair.apply(
        ReplanDecision(kind=TriggerKind.REPLAN_SUBTREE, subtree_root_id="L2-1"),
        c,
    )
    assert out["applied"] is False
    assert g.replans_used == 1


def test_apply_replan_root_replaces_plan():
    c = PlanCursor(_plan_two_branches())
    new_plan = {
        "id": "ROOT2",
        "intent": "new intent",
        "type": "composite",
        "sub_plans": [
            {"id": "newroot", "type": "atomic", "is_atomic": True, "task": "AnswerFAQ", "sub_plans": []},
        ],
        "execution_logic": [],
    }
    repair = PlanRepair(
        budget_guard=BudgetGuard(),
        root_planner_callback=lambda intent: new_plan,
    )
    out = repair.apply(
        ReplanDecision(kind=TriggerKind.REPLAN_ROOT),
        c,
        intent="new intent",
    )
    assert out["applied"] is True
    # 舊 atomic 全被 SKIPPED
    assert c.record("a").state == NodeState.SKIPPED
    assert c.record("b").state == NodeState.SKIPPED
    # 新 atomic ready
    ready = [r.node_id for r in c.next_ready_atomics()]
    assert any(rid.endswith("newroot") for rid in ready)


def test_apply_abort_returns_applied_true():
    c = PlanCursor(_plan_two_branches())
    out = PlanRepair().apply(ReplanDecision.abort("any"), c)
    assert out["applied"] is True
    assert out["kind"] == "abort"

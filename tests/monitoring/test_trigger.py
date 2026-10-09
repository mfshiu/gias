"""ReplanTrigger 單元測試。"""

from __future__ import annotations

from src.core.monitoring.budget import Budget, BudgetGuard
from src.core.monitoring.cursor import PlanCursor
from src.core.monitoring.events import (
    ActionResult,
    EnvChange,
    NodeState,
    TriggerKind,
)
from src.core.monitoring.trigger import ReplanTrigger, TriggerConfig


def _two_atomic_plan() -> dict:
    return {
        "id": "root",
        "intent": "root",
        "type": "composite",
        "sub_plans": [
            {
                "id": "L2-1",
                "type": "composite",
                "sub_plans": [
                    {"id": "a", "type": "atomic", "is_atomic": True, "task": "T1",
                     "intent": "前往 AI_Tech_Area",
                     "params": {"target_name": "AI_Tech_Area"},
                     "sub_plans": []},
                ],
                "execution_logic": [],
            },
            {
                "id": "L2-2",
                "type": "composite",
                "sub_plans": [
                    {"id": "b", "type": "atomic", "is_atomic": True, "task": "T2",
                     "intent": "前往 Robotics_Area",
                     "params": {"target_name": "Robotics_Area"},
                     "sub_plans": []},
                ],
                "execution_logic": [],
            },
        ],
        "execution_logic": [{"type": "Sequence", "from_id": "L2-1", "to_id": "L2-2"}],
    }


def test_decide_none_when_no_signals():
    c = PlanCursor(_two_atomic_plan())
    g = BudgetGuard()
    t = ReplanTrigger(budget_guard=g)
    assert t.decide(action_results=[], env_changes=[], cursor=c).kind == TriggerKind.NONE


def test_decide_retry_when_node_failed_and_retry_available():
    c = PlanCursor(_two_atomic_plan())
    tid = c.mark_dispatched("a")
    c.mark_failed("a", "boom")
    g = BudgetGuard(Budget(max_retries_per_node=1))
    t = ReplanTrigger(budget_guard=g)
    ar = ActionResult(task_id=tid, node_id="a", ok=False, error="boom")
    decision = t.decide(action_results=[ar], env_changes=[], cursor=c)
    assert decision.kind == TriggerKind.RETRY_NODE
    assert "a" in decision.affected_node_ids


def test_decide_subtree_when_retry_exhausted():
    c = PlanCursor(_two_atomic_plan())
    tid = c.mark_dispatched("a")
    c.mark_failed("a", "boom")
    g = BudgetGuard(Budget(max_retries_per_node=1))
    g.consume_retry("a")  # 用掉重試配額
    t = ReplanTrigger(budget_guard=g)
    ar = ActionResult(task_id=tid, node_id="a", ok=False, error="boom")
    decision = t.decide(action_results=[ar], env_changes=[], cursor=c)
    assert decision.kind == TriggerKind.REPLAN_SUBTREE
    assert decision.subtree_root_id == "L2-1"


def test_decide_subtree_replan_on_env_change():
    c = PlanCursor(_two_atomic_plan())
    g = BudgetGuard()
    t = ReplanTrigger(budget_guard=g)
    change = EnvChange(
        topic="Zone/AI_Tech_Area/CURRENT_STATE",
        action="update",
        new_value={"status_name": "Closed"},
    )
    decision = t.decide(action_results=[], env_changes=[change], cursor=c)
    assert decision.kind == TriggerKind.REPLAN_SUBTREE
    assert decision.subtree_root_id == "L2-1"
    assert "a" in decision.affected_node_ids


def test_decide_root_replan_when_multiple_composites_affected():
    c = PlanCursor(_two_atomic_plan())
    g = BudgetGuard()
    t = ReplanTrigger(budget_guard=g)
    changes = [
        EnvChange(topic="Zone/AI_Tech_Area/CURRENT_STATE", action="update", new_value={"status_name": "Closed"}),
        EnvChange(topic="Zone/Robotics_Area/CURRENT_STATE", action="update", new_value={"status_name": "Crowded"}),
    ]
    decision = t.decide(action_results=[], env_changes=changes, cursor=c)
    assert decision.kind == TriggerKind.REPLAN_ROOT


def test_decide_abort_on_excessive_failures():
    c = PlanCursor(_two_atomic_plan())
    # 把兩個 atomic 都標 FAILED
    for nid in ("a", "b"):
        c.mark_dispatched(nid)
        c.mark_failed(nid, "boom")
    g = BudgetGuard()
    cfg = TriggerConfig(abort_after_failures=2)
    t = ReplanTrigger(config=cfg, budget_guard=g)
    decision = t.decide(action_results=[], env_changes=[], cursor=c)
    assert decision.kind == TriggerKind.ABORT


def test_decide_abort_when_deadline_reached():
    import time
    c = PlanCursor(_two_atomic_plan())
    g = BudgetGuard(Budget(deadline_sec=0.001))
    time.sleep(0.01)
    t = ReplanTrigger(budget_guard=g)
    decision = t.decide(action_results=[], env_changes=[], cursor=c)
    assert decision.kind == TriggerKind.ABORT


def test_unrelated_env_change_ignored():
    c = PlanCursor(_two_atomic_plan())
    t = ReplanTrigger(budget_guard=BudgetGuard())
    change = EnvChange(topic="Unrelated/Foo/Bar", action="update")
    decision = t.decide(action_results=[], env_changes=[change], cursor=c)
    assert decision.kind == TriggerKind.NONE


def test_env_change_not_matching_any_node_returns_none():
    c = PlanCursor(_two_atomic_plan())
    t = ReplanTrigger(budget_guard=BudgetGuard())
    change = EnvChange(topic="Zone/Unknown_Zone/CURRENT_STATE", action="update")
    decision = t.decide(action_results=[], env_changes=[change], cursor=c)
    assert decision.kind == TriggerKind.NONE

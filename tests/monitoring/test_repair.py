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


# ----------------------------------------------------------------------
# L-02：重規劃脈絡（原因、受影響步驟、環境事實）要傳給 planner
# ----------------------------------------------------------------------
def _failed_a_cursor():
    c = PlanCursor(_plan_two_branches())
    tid = c.mark_dispatched("a")
    c.accept_action_result(tid, {"task_id": tid, "ok": False, "error": "area closed"})
    return c


def _subtree_decision():
    return ReplanDecision(kind=TriggerKind.REPLAN_SUBTREE, affected_node_ids=("a",),
                          subtree_root_id="L2-1", reason="node a failed; retries exhausted")


def _new_subtree():
    return {"id": "N", "sub_plans": [
        {"id": "na", "type": "atomic", "is_atomic": True, "task": "SuggestRoute", "sub_plans": []},
    ], "execution_logic": []}


def test_subtree_planner_receives_replan_context_with_env_facts():
    c = _failed_a_cursor()
    captured = {}

    def planner(sub_intent, context):
        captured["context"] = context
        return _new_subtree()

    facts = {"zone_states": [{"zone": "AI_Tech_Area", "state": "Closed"}]}
    repair = PlanRepair(budget_guard=BudgetGuard(), planner_callback=planner, env_facts_provider=lambda: facts)
    assert repair.apply(_subtree_decision(), c)["applied"] is True

    ctx = captured["context"]
    assert ctx["env_facts"] == facts
    assert ctx["replan"]["kind"] == "replan_subtree"
    assert ctx["replan"]["reason"] == "node a failed; retries exhausted"
    step = ctx["replan"]["affected_steps"][0]
    assert (step["id"], step["task"], step["error"]) == ("a", "LocateExhibit", "area closed")
    assert step["params"] == {"target_name": "AI"}


def test_env_facts_provider_failure_still_replans():
    c = _failed_a_cursor()
    captured = {}

    def planner(sub_intent, context):
        captured["context"] = context
        return _new_subtree()

    def broken():
        raise RuntimeError("blackboard down")

    repair = PlanRepair(budget_guard=BudgetGuard(), planner_callback=planner, env_facts_provider=broken)
    assert repair.apply(_subtree_decision(), c)["applied"] is True
    assert "env_facts" not in captured["context"]


def test_root_planner_with_context_parameter_receives_context():
    c = _failed_a_cursor()
    captured = {}

    def root_planner(intent, context):
        captured["context"] = context
        return {"id": "R", "type": "composite", "execution_logic": [], "sub_plans": [
            {"id": "n1", "type": "atomic", "is_atomic": True, "task": "AnswerFAQ", "sub_plans": []}]}

    repair = PlanRepair(budget_guard=BudgetGuard(), root_planner_callback=root_planner,
                        env_facts_provider=lambda: {"closed_booths": []})
    decision = ReplanDecision(kind=TriggerKind.REPLAN_ROOT, affected_node_ids=("a",), reason="r")
    assert repair.apply(decision, c, intent="i")["applied"] is True
    assert captured["context"]["replan"]["kind"] == "replan_root"
    # 空的事實（{"closed_booths": []}）仍是有效資訊，不可被丟掉
    assert captured["context"]["env_facts"] == {"closed_booths": []}


def test_repair_pending_node_updates_params_without_consuming_retry():
    c = PlanCursor(_plan_two_branches())
    g = BudgetGuard(Budget(max_retries_per_node=1))
    out = PlanRepair(budget_guard=g).apply(
        ReplanDecision(kind=TriggerKind.REPAIR_NODE, affected_node_ids=("b",), new_params={"target_name": "Y"}), c)
    assert out["applied"] is True
    assert c.record("b").state == NodeState.PENDING
    assert c.record("b").node["params"]["target_name"] == "Y"
    assert g.retries_left("b") == 1

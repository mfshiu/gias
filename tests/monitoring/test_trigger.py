"""ReplanTrigger 單元測試。"""

from __future__ import annotations

import pytest

from src.core.monitoring.budget import Budget, BudgetGuard
from src.core.monitoring.cursor import PlanCursor
from src.core.monitoring.events import (
    ActionResult,
    EnvChange,
    NodeState,
    ReplanDecision,
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


# ----------------------------------------------------------------------
# L-01：BlackboardWatcher 的實際事件格式要能對應到節點
# ----------------------------------------------------------------------
def test_watcher_relationship_event_matches_node_via_source_id():
    """關係事件 topic 為 Zone/CURRENT_STATE/State，區域名稱只在 metadata.source_id。"""
    c = PlanCursor(_two_atomic_plan())
    t = ReplanTrigger(budget_guard=BudgetGuard())
    change = EnvChange(
        topic="Zone/CURRENT_STATE/State",
        action="create",
        new_value={"source_id": "AI_Tech_Area", "target_id": "Crowded"},
        metadata={"source_label": "Zone", "source_id": "AI_Tech_Area", "rel_type": "CURRENT_STATE",
                  "target_label": "State", "target_id": "Crowded"},
    )
    decision = t.decide(action_results=[], env_changes=[change], cursor=c)
    assert decision.kind == TriggerKind.REPLAN_SUBTREE
    assert decision.affected_node_ids == ("a",)


# ----------------------------------------------------------------------
# L-03：LLM 輔助判斷
# ----------------------------------------------------------------------
class _RecordingDecider:
    def __init__(self, decision=None, exc=None):
        self.decision = decision
        self.exc = exc
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.decision


def _failed_cursor():
    c = PlanCursor(_two_atomic_plan())
    tid = c.mark_dispatched("a")
    c.mark_failed("a", "invalid target_name")
    return c, ActionResult(task_id=tid, node_id="a", ok=False, error="invalid target_name")


def _llm_trigger(decider, *, guard=None, **cfg):
    return ReplanTrigger(
        config=TriggerConfig(enable_llm_assist=True, **cfg),
        budget_guard=guard or BudgetGuard(Budget(max_retries_per_node=1)),
        llm_decider=decider,
    )


def test_llm_can_choose_repair_for_failed_node():
    c, ar = _failed_cursor()
    repair = ReplanDecision(kind=TriggerKind.REPAIR_NODE, affected_node_ids=("a",),
                            new_params={"target_name": "AI_Zone_B"}, reason="llm: wrong id")
    decider = _RecordingDecider(repair)
    decision = _llm_trigger(decider).decide(action_results=[ar], env_changes=[], cursor=c, intent="去 AI 區")
    assert decision.kind == TriggerKind.REPAIR_NODE
    assert decision.new_params == {"target_name": "AI_Zone_B"}
    assert decider.calls[0]["action_results"] == [ar]


def test_llm_decision_beyond_retry_budget_falls_back_to_rules():
    c, ar = _failed_cursor()
    guard = BudgetGuard(Budget(max_retries_per_node=1))
    guard.consume_retry("a")
    retry = ReplanDecision(kind=TriggerKind.RETRY_NODE, affected_node_ids=("a",))
    decision = _llm_trigger(_RecordingDecider(retry), guard=guard).decide(
        action_results=[ar], env_changes=[], cursor=c)
    # LLM 要求 retry，但配額已用完 → 回到規則：升級為子樹重規劃
    assert decision.kind == TriggerKind.REPLAN_SUBTREE


@pytest.mark.parametrize("decider", [
    _RecordingDecider(exc=RuntimeError("llm down")),
    _RecordingDecider(ReplanDecision.none()),
])
def test_llm_failure_or_none_falls_back_to_rules(decider):
    c, ar = _failed_cursor()
    decision = _llm_trigger(decider).decide(action_results=[ar], env_changes=[], cursor=c)
    assert decision.kind == TriggerKind.RETRY_NODE


def test_llm_not_consulted_when_disabled():
    c, ar = _failed_cursor()
    decider = _RecordingDecider(exc=AssertionError("should not be called"))
    t = ReplanTrigger(budget_guard=BudgetGuard(), llm_decider=decider)
    assert t.decide(action_results=[ar], env_changes=[], cursor=c).kind == TriggerKind.RETRY_NODE
    assert decider.calls == []


def test_llm_consulted_for_relevant_env_change_rules_cannot_match():
    """區域寫法與節點參數不同（規則比對不到）時，交給 LLM 判斷。"""
    c = PlanCursor(_two_atomic_plan())
    replan = ReplanDecision(kind=TriggerKind.REPLAN_SUBTREE, affected_node_ids=("a",), subtree_root_id="L2-1")
    decider = _RecordingDecider(replan)
    change = EnvChange(topic="Zone/智慧科技區/crowd_level", action="update", new_value="Crowded")
    decision = _llm_trigger(decider).decide(action_results=[], env_changes=[change], cursor=c)
    assert decision.kind == TriggerKind.REPLAN_SUBTREE
    assert decider.calls[0]["env_changes"] == [change]


def test_llm_not_consulted_for_irrelevant_env_change():
    c = PlanCursor(_two_atomic_plan())
    decider = _RecordingDecider(ReplanDecision.abort("x"))
    change = EnvChange(topic="Agent/GuideBot/status", action="update")
    assert _llm_trigger(decider).decide(action_results=[], env_changes=[change], cursor=c).kind == TriggerKind.NONE
    assert decider.calls == []


def test_llm_max_calls_caps_consultations():
    c = PlanCursor(_two_atomic_plan())
    decider = _RecordingDecider(ReplanDecision.none())
    t = _llm_trigger(decider, llm_max_calls=1)
    change = EnvChange(topic="Zone/智慧科技區/crowd_level", action="update")
    for _ in range(3):
        t.decide(action_results=[], env_changes=[change], cursor=c)
    assert len(decider.calls) == 1

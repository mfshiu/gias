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
    t = ReplanTrigger(config=TriggerConfig(relevant_topic_patterns=("Zone/*/*",)), budget_guard=BudgetGuard())
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
    trigger = _llm_trigger(decider, relevant_topic_patterns=("Zone/*/*", "Booth/*/*"))
    assert trigger.decide(action_results=[], env_changes=[change], cursor=c).kind == TriggerKind.NONE
    assert decider.calls == []


def test_llm_max_calls_caps_consultations():
    c = PlanCursor(_two_atomic_plan())
    decider = _RecordingDecider(ReplanDecision.none())
    t = _llm_trigger(decider, llm_max_calls=1)
    change = EnvChange(topic="Zone/智慧科技區/crowd_level", action="update")
    for _ in range(3):
        t.decide(action_results=[], env_changes=[change], cursor=c)
    assert len(decider.calls) == 1


# ----------------------------------------------------------------------
# C-08：以完整名稱比對環境事件與節點（不用子字串）
# ----------------------------------------------------------------------
def _booth_plan(*targets) -> dict:
    return {
        "id": "root", "type": "composite", "execution_logic": [],
        "sub_plans": [
            {"id": f"n{i}", "type": "atomic", "is_atomic": True, "task": "LocateExhibit",
             "intent": f"前往 {t}", "params": {"target_name": t, "current_location": "P_Entrance"},
             "sub_plans": []}
            for i, t in enumerate(targets, start=1)
        ],
    }


def _booth_event(booth_id: str) -> EnvChange:
    return EnvChange(topic=f"Booth/{booth_id}/status", action="update", new_value="closed",
                     metadata={"label": "Booth", "node_id": booth_id, "property": "status"})


def test_booth_event_does_not_match_longer_id():
    """舊行為："A1" in "前往 A12" 為 True → 不必要的子樹重規劃。"""
    c = PlanCursor(_booth_plan("A12"))
    t = ReplanTrigger(config=TriggerConfig(relevant_topic_prefixes=("Booth/",)), budget_guard=BudgetGuard())
    assert t.decide(action_results=[], env_changes=[_booth_event("A1")], cursor=c).kind == TriggerKind.NONE


def test_booth_event_matches_exact_id_only():
    c = PlanCursor(_booth_plan("A12", "A1"))
    t = ReplanTrigger(config=TriggerConfig(relevant_topic_prefixes=("Booth/",)), budget_guard=BudgetGuard())
    decision = t.decide(action_results=[], env_changes=[_booth_event("A1")], cursor=c)
    assert decision.kind == TriggerKind.REPLAN_SUBTREE
    assert decision.affected_node_ids == ("n2",)


def test_zone_event_matches_alias_and_contained_booth():
    c = PlanCursor(_booth_plan("AI 展區", "TechCorp AI", "B_VR1"))
    cfg = TriggerConfig(
        relevant_topic_prefixes=("Zone/",),
        entity_aliases={"AI_Tech_Area": ["AI 展區"], "B_AI1": ["TechCorp AI"]},
        entity_parents={"B_AI1": "AI_Tech_Area", "B_VR1": "VR_Area"},
    )
    change = EnvChange(topic="Zone/CURRENT_STATE/State", action="create",
                       metadata={"source_id": "AI_Tech_Area", "rel_type": "CURRENT_STATE", "target_id": "Closed"})
    decision = ReplanTrigger(config=cfg, budget_guard=BudgetGuard()).decide(
        action_results=[], env_changes=[change], cursor=c)
    assert decision.kind == TriggerKind.REPLAN_SUBTREE
    assert decision.affected_node_ids == ("n1", "n2")


def test_origin_parameter_is_ignored():
    """步驟的出發點不代表依賴該地點：入口所在區的事件不應觸發重規劃。"""
    c = PlanCursor(_booth_plan("B_VR1"))
    cfg = TriggerConfig(relevant_topic_prefixes=("Zone/",), entity_parents={"P_Entrance": "Main_Hall"})
    change = EnvChange(topic="Zone/Main_Hall/crowd_level", action="update", new_value="Crowded",
                       metadata={"label": "Zone", "node_id": "Main_Hall", "property": "crowd_level"})
    t = ReplanTrigger(config=cfg, budget_guard=BudgetGuard())
    assert t.decide(action_results=[], env_changes=[change], cursor=c).kind == TriggerKind.NONE


def test_relationship_name_in_topic_is_not_an_entity():
    c = PlanCursor(_booth_plan("CURRENT_STATE"))
    change = EnvChange(topic="Zone/CURRENT_STATE/State", action="create",
                       metadata={"source_id": "VR_Area", "rel_type": "CURRENT_STATE", "target_id": "Closed"})
    t = ReplanTrigger(config=TriggerConfig(relevant_topic_prefixes=("Zone/",)), budget_guard=BudgetGuard())
    assert t.decide(action_results=[], env_changes=[change], cursor=c).kind == TriggerKind.NONE


# ----------------------------------------------------------------------
# L-04：執行端放寬限制時交給 LLM 判斷
# ----------------------------------------------------------------------
def _relaxed(task_id):
    from src.core.monitoring.events import ExecutorAdaptation
    return ExecutorAdaptation(task_id=task_id, node_id="a", kind="constraint_relaxed",
                              reason="no route satisfies avoid_crowded", detail={"constraint": "avoid_crowded"})


def test_constraint_relaxation_is_escalated_to_llm():
    c = PlanCursor(_two_atomic_plan())
    tid = c.mark_dispatched("a")
    replan = ReplanDecision(kind=TriggerKind.REPLAN_SUBTREE, affected_node_ids=("a",), subtree_root_id="L2-1")
    decider = _RecordingDecider(replan)
    decision = _llm_trigger(decider).decide(action_results=[], env_changes=[], cursor=c, adaptations=[_relaxed(tid)])
    assert decision.kind == TriggerKind.REPLAN_SUBTREE
    assert decider.calls[0]["adaptations"][0].kind == "constraint_relaxed"


def test_constraint_relaxation_is_accepted_without_llm():
    c = PlanCursor(_two_atomic_plan())
    tid = c.mark_dispatched("a")
    t = ReplanTrigger(budget_guard=BudgetGuard())
    assert t.decide(action_results=[], env_changes=[], cursor=c, adaptations=[_relaxed(tid)]).kind == TriggerKind.NONE


def test_reroute_alone_does_not_consult_llm():
    from src.core.monitoring.events import ExecutorAdaptation
    c = PlanCursor(_two_atomic_plan())
    tid = c.mark_dispatched("a")
    decider = _RecordingDecider(ReplanDecision.abort("x"))
    reroute = ExecutorAdaptation(task_id=tid, node_id="a", kind="reroute")
    assert _llm_trigger(decider).decide(action_results=[], env_changes=[], cursor=c,
                                        adaptations=[reroute]).kind == TriggerKind.NONE
    assert decider.calls == []


# ----------------------------------------------------------------------
# A-02：前置條件不成立的失敗不重試
# ----------------------------------------------------------------------
def test_precondition_failure_skips_retry():
    c = PlanCursor(_two_atomic_plan())
    tid = c.mark_dispatched("a")
    c.accept_action_result(tid, {"task_id": tid, "ok": False, "error": "precondition failed: zone closed"})
    t = ReplanTrigger(budget_guard=BudgetGuard(Budget(max_retries_per_node=1)))
    ar = ActionResult(task_id=tid, node_id="a", ok=False, error="precondition failed: zone closed")
    decision = t.decide(action_results=[ar], env_changes=[], cursor=c)
    assert decision.kind == TriggerKind.REPLAN_SUBTREE


# ----------------------------------------------------------------------
# G-02：觸發條件不綁定展場領域
# ----------------------------------------------------------------------
from src.core.monitoring.trigger import topic_matches


@pytest.mark.parametrize("pattern, topic, expected", [
    ("Zone/*/*", "Zone/AI_Tech_Area/crowd_level", True),
    ("Zone/*/*", "Zone/CURRENT_STATE/State", True),
    ("Zone/*/*", "ZoneX/AI_Tech_Area/crowd_level", False),    # 前綴比對會誤中的情況
    ("Zone/*/*", "Zone/AI_Tech_Area", False),                 # 段數不同
    ("*/CONNECTED_TO/*", "POI/CONNECTED_TO/POI", True),
    ("*/CONNECTED_TO/*", "Zone/A12/status", False),           # 舊作法把它當成空前綴，什麼都比對得到
    ("Booth/B_AI1/status", "Booth/B_AI1/status", True),
])
def test_topic_matches_uses_subscription_semantics(pattern, topic, expected):
    assert topic_matches(pattern, topic) is expected


def _room_plan():
    return {"id": "root", "type": "composite", "intent": "準備會議", "execution_logic": [], "sub_plans": [
        {"id": "a", "type": "atomic", "is_atomic": True, "task": "SetupProjector", "intent": "在 R301 準備投影機",
         "params": {"room": "R301"}, "sub_plans": []},
    ]}


def test_default_config_is_not_tied_to_the_venue_domain():
    assert TriggerConfig().relevant_topic_patterns == () and TriggerConfig().relevant_topic_prefixes == ()
    c = PlanCursor(_room_plan())
    change = EnvChange(topic="Room/R301/temperature", action="update", metadata={"node_id": "R301"})
    decision = ReplanTrigger(budget_guard=BudgetGuard()).decide(action_results=[], env_changes=[change], cursor=c)
    assert decision.kind == TriggerKind.REPLAN_SUBTREE and decision.affected_node_ids == ("a",)


def test_patterns_filter_by_segments():
    c = PlanCursor(_two_atomic_plan())
    t = ReplanTrigger(config=TriggerConfig(relevant_topic_patterns=("Zone/*/*",)), budget_guard=BudgetGuard())
    lookalike = EnvChange(topic="ZoneX/AI_Tech_Area/crowd_level", action="update")
    assert t.decide(action_results=[], env_changes=[lookalike], cursor=c).kind == TriggerKind.NONE
    real = EnvChange(topic="Zone/AI_Tech_Area/crowd_level", action="update")
    assert t.decide(action_results=[], env_changes=[real], cursor=c).kind == TriggerKind.REPLAN_SUBTREE


def test_entity_metadata_keys_are_configurable():
    c = PlanCursor(_room_plan())
    # 其他事件來源：實體放在 metadata.room_id，topic 中段不是房間
    change = EnvChange(topic="Facility/HVAC/alarm", action="update", metadata={"room_id": "R301"})
    plain = ReplanTrigger(budget_guard=BudgetGuard())
    assert plain.decide(action_results=[], env_changes=[change], cursor=c).kind == TriggerKind.NONE
    cfg = TriggerConfig(entity_metadata_keys=("node_id", "source_id", "room_id"))
    decision = ReplanTrigger(config=cfg, budget_guard=BudgetGuard()).decide(
        action_results=[], env_changes=[change], cursor=c)
    assert decision.kind == TriggerKind.REPLAN_SUBTREE

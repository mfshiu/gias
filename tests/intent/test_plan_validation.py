"""IntentionalAgent 規劃驗證單元測試。

- C-02：無法派工的節點（沒有 task 的 atomic、沒有子節點的 composite）不可放行
- C-01：節點 id 重複的 plan 不可放行
- C-03：ScopeGate 失敗時依 scope_gate_strict 決定拒絕或放行
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.app_helper import get_agent_config
from src.core.intent.scope_gate import ScopeDecision, ScopeGateError
from src.core.intent.sub_intent import SubIntent
from src.core.intentional_agent import IntentionalAgent


def _minimal_agent_config() -> dict:
    try:
        cfg = get_agent_config()
        if cfg.get("llm") and cfg.get("broker"):
            return dict(cfg)
    except Exception:
        pass
    return {
        "llm": {"provider": "mock"},
        "broker": {"broker_name": "mqtt01"},
        "broker.mqtt01": {"broker_type": "mqtt", "host": "localhost", "port": 1883},
        "kg": {"type": "neo4j", "neo4j": {"uri": "bolt://localhost:7687"}, "neo4j_actions": {}},
    }


def _make_agent(intent_cfg: dict | None = None) -> IntentionalAgent:
    cfg = _minimal_agent_config()
    if intent_cfg is not None:
        cfg["intent"] = {**(cfg.get("intent") or {}), **intent_cfg}
    with patch("src.core.intentional_agent.Neo4jBoltAdapter") as mock_adapter_cls:
        mock_adapter_cls.from_config.return_value = MagicMock()
        return IntentionalAgent(agent_config=cfg, intention="test")


def _atomic(node_id, task="TaskA", action="TaskA()"):
    node = {"id": node_id, "type": "atomic", "is_atomic": True, "intent": f"i-{node_id}",
            "action": action, "sub_plans": []}
    if task is not None:
        node.update({"task": task, "topic": "info.request"})
    return node


def _root(children):
    return {"id": "root", "type": "composite", "intent": "root", "sub_plans": children, "execution_logic": []}


# ----------------------------------------------------------------------
# _find_unexecutable_nodes
# ----------------------------------------------------------------------
def test_executable_plan_has_no_findings():
    assert IntentionalAgent._find_unexecutable_nodes(_root([_atomic("1"), _atomic("2")])) == []


def test_atomic_without_task_is_unexecutable():
    leaf_no_children = {"id": "2", "type": "leaf_no_children", "is_atomic": True, "sub_plans": []}
    leaf_forced = {"id": "3", "type": "leaf_forced_atomic", "is_atomic": True,
                   "atomic_source": "new_generated"}
    found = IntentionalAgent._find_unexecutable_nodes(
        _root([_atomic("1"), leaf_no_children, leaf_forced, _atomic("4", task="Unknown")])
    )
    assert [f["id"] for f in found] == ["2", "3", "4"]
    assert {f["reason"] for f in found} == {"no_bound_task"}


def test_composite_without_children_is_unexecutable():
    failed = {"id": "2", "type": "composite", "intent": "x", "sub_plans": [], "error": "Decomposition failed"}
    found = IntentionalAgent._find_unexecutable_nodes(_root([_atomic("1"), failed]))
    assert found == [{"id": "2", "type": "composite", "intent": "x", "reason": "Decomposition failed"}]


# ----------------------------------------------------------------------
# plan_intention 整合（其餘元件以 stub 取代）
# ----------------------------------------------------------------------
def _plan_intention_with(agent: IntentionalAgent, planned: dict) -> dict:
    sub = SubIntent(intent="do A")
    agent.break_down_intention = lambda intention: [sub]
    agent._match_subs = lambda subs: ([(sub, [object()])], [])
    agent.selector = MagicMock()
    agent.selector.select_actions.return_value = {"TaskA()": "do A"}
    agent.scope_gate = MagicMock()
    agent.scope_gate.decide.return_value = ScopeDecision(can_execute=True, reason="ok")
    agent.planner = MagicMock()
    agent.planner.plan.return_value = planned
    return agent.plan_intention("do A")


def test_plan_intention_accepts_executable_plan():
    plan = _plan_intention_with(_make_agent(), _root([_atomic("1")]))
    assert plan["type"] == "composite"


def test_plan_intention_rejects_plan_with_unexecutable_node():
    planned = _root([_atomic("1"), {"id": "2", "type": "leaf_forced_atomic", "is_atomic": True}])
    plan = _plan_intention_with(_make_agent(), planned)
    assert plan["type"] == "leaf_unresolved"
    assert [n["id"] for n in plan["debug"]["unexecutable_nodes"]] == ["2"]


def test_plan_intention_rejects_duplicate_node_ids():
    nested = {"id": "2", "type": "composite", "intent": "B", "execution_logic": [],
              "sub_plans": [_atomic("1"), _atomic("2.2")]}
    plan = _plan_intention_with(_make_agent(), _root([_atomic("1"), nested]))
    assert plan["type"] == "leaf_unresolved"
    assert plan["debug"]["duplicate_node_ids"] == ["1"]


# ----------------------------------------------------------------------
# C-03：scope_gate_strict
# ----------------------------------------------------------------------
@pytest.mark.parametrize("strict, rejected", [(True, True), (False, False)])
def test_scope_gate_failure_follows_strict_setting(strict, rejected):
    agent = _make_agent({"enable_scope_gate": True, "scope_gate_strict": strict})
    agent.scope_gate = MagicMock()
    agent.scope_gate.decide.side_effect = ScopeGateError("LLM call failed")
    out = agent._run_scope_gate("do A", [SubIntent(intent="do A")], {"TaskA()": "do A"}, {"TaskA"})
    if rejected:
        assert out is not None and out["type"] == "leaf_unresolved"
        assert "Scope gate failed" in out["reason"]
    else:
        assert out is None


# ----------------------------------------------------------------------
# C-05：有依賴循環的計畫要拒絕
# ----------------------------------------------------------------------
def test_plan_intention_rejects_cyclic_dependencies():
    planned = _root([_atomic("1"), _atomic("2")])
    planned["execution_logic"] = [{"type": "Sequence", "from_id": "1", "to_id": "2"},
                                  {"type": "Sequence", "from_id": "2", "to_id": "1"}]
    plan = _plan_intention_with(_make_agent(), planned)
    assert plan["type"] == "leaf_unresolved"
    assert plan["debug"]["dependency_cycles"] == [{"composite_id": "root", "node_ids": ["1", "2"]}]


# ----------------------------------------------------------------------
# C-07：參數問題（warn 記錄後照常執行；strict 拒絕）
# ----------------------------------------------------------------------
def _with_param_issue():
    node = _atomic("1")
    node["param_issues"] = [{"param": "interests", "issue": "missing_required"},
                            {"param": "color", "issue": "unknown_param", "value": "red"}]
    return _root([node])


def test_param_issues_are_recorded_in_warn_mode():
    plan = _plan_intention_with(_make_agent(), _with_param_issue())
    assert plan["type"] == "composite"
    assert [i["issue"] for i in plan["debug"]["param_issues"]] == ["missing_required", "unknown_param"]
    assert plan["debug"]["param_issues"][0]["task"] == "TaskA"


def test_param_issues_reject_plan_in_strict_mode():
    agent = _make_agent()
    agent.agent_config = {**agent.agent_config, "intent": {**(agent.agent_config.get("intent") or {}),
                                                           "param_validation": "strict"}}
    plan = _plan_intention_with(agent, _with_param_issue())
    assert plan["type"] == "leaf_unresolved"
    assert plan["reason"] == "Planner produced invalid action parameters."


def test_strict_mode_ignores_unknown_params_only():
    agent = _make_agent()
    agent.agent_config = {**agent.agent_config, "intent": {"param_validation": "strict"}}
    node = _atomic("1")
    node["param_issues"] = [{"param": "color", "issue": "unknown_param", "value": "red"}]
    assert _plan_intention_with(agent, _root([node]))["type"] == "composite"


# ----------------------------------------------------------------------
# C-06：比對門檻可由 intent.matching 設定
# ----------------------------------------------------------------------
def test_matching_thresholds_come_from_config():
    agent = _make_agent({"matching": {"min_score": 0.8, "fallback_min_score": 0.65}})
    assert (agent.matcher.min_score, agent.matcher.fallback_min_score) == (0.8, 0.65)
    default = _make_agent()
    assert (default.matcher.min_score, default.matcher.fallback_min_score) == (0.75, 0.6)


# ----------------------------------------------------------------------
# A-02：前置條件（LLM 提出計畫、KG 驗證）
# ----------------------------------------------------------------------
_ZONE_OPEN = {"key": "zone_state", "arg": "target_name", "op": "not_in", "value": ["Closed"],
              "desc": "目的地所在區域沒有關閉"}


def _nav(target, severity="block"):
    node = _atomic("1")
    node.update(params={"target_name": target}, preconditions=[dict(_ZONE_OPEN, severity=severity)])
    return _root([node])


def _with_state(agent, state):
    agent._state_probe = lambda: (lambda key, arg: (True, state.get((key, arg))))


def _plan_with_sequence(agent, *plans):
    sub = SubIntent(intent="go")
    agent.break_down_intention = lambda intention: [sub]
    agent._match_subs = lambda subs: ([(sub, [object()])], [])
    agent.selector = MagicMock()
    agent.selector.select_actions.return_value = {"TaskA()": "go"}
    agent.scope_gate = MagicMock()
    agent.scope_gate.decide.return_value = ScopeDecision(can_execute=True, reason="ok")
    agent.planner = MagicMock()
    agent.planner.plan.side_effect = list(plans)
    return agent.plan_intention("go")


def test_precondition_violation_triggers_one_corrective_replan():
    agent = _make_agent()
    _with_state(agent, {("zone_state", "AI_Tech_Area"): "Closed", ("zone_state", "Robotics_Area"): "Normal"})
    plan = _plan_with_sequence(agent, _nav("AI_Tech_Area"), _nav("Robotics_Area"))
    assert plan["type"] == "composite"
    assert plan["sub_plans"][0]["params"]["target_name"] == "Robotics_Area"
    # 第二次規劃帶著違反原因
    ctx = agent.planner.plan.call_args_list[1].kwargs["context"]
    assert ctx["replan"]["kind"] == "precondition"
    assert ctx["replan"]["affected_steps"][0]["error"].startswith("precondition failed: 目的地所在區域沒有關閉")


def test_plan_rejected_when_corrected_plan_still_violates():
    agent = _make_agent()
    _with_state(agent, {("zone_state", "AI_Tech_Area"): "Closed"})
    plan = _plan_with_sequence(agent, _nav("AI_Tech_Area"), _nav("AI_Tech_Area"))
    assert plan["type"] == "leaf_unresolved"
    assert plan["reason"] == "Plan violates action preconditions."
    assert plan["debug"]["precondition_findings"][0]["actual"] == "Closed"
    assert agent.planner.plan.call_count == 2


def test_warn_precondition_is_recorded_not_replanned():
    agent = _make_agent()
    _with_state(agent, {("zone_state", "AI_Tech_Area"): "Closed"})
    plan = _plan_with_sequence(agent, _nav("AI_Tech_Area", severity="warn"))
    assert plan["type"] == "composite"
    assert plan["debug"]["precondition_findings"][0]["severity"] == "warn"
    assert agent.planner.plan.call_count == 1


def test_preconditions_can_be_disabled():
    agent = _make_agent({"preconditions": {"enabled": False}})
    _with_state(agent, {("zone_state", "AI_Tech_Area"): "Closed"})
    assert _plan_with_sequence(agent, _nav("AI_Tech_Area"))["type"] == "composite"


def test_resolve_entity_uses_domain_aliases():
    from src.core.intent.domain_profile import DomainProfile

    agent = _make_agent()
    agent.domain = DomainProfile(entity_aliases={"AI_Tech_Area": ["AI 展區"]})
    assert agent._resolve_entity("AI展區") == "AI_Tech_Area"
    assert agent._resolve_entity("ai_tech_area") == "AI_Tech_Area"
    assert agent._resolve_entity("B_GM1") == "B_GM1"


# ----------------------------------------------------------------------
# A-03：步驟之間的資料傳遞
# ----------------------------------------------------------------------
def _with_input(source="1", output="booths", required=True):
    rec = _atomic("1", task="RecommendExhibits")
    rec["outputs"] = [{"key": "booths", "type": "list[string]"}]
    route = _atomic("2", task="SuggestRoute")
    route.update(params={}, params_schema=[{"key": "waypoints", "type": "list[string]", "required": required}],
                 inputs=[{"param": "waypoints", "from": source, "output": output}])
    return _root([rec, route])


def test_plan_orders_steps_by_their_data_dependencies():
    agent = _make_agent()
    plan = _plan_with_sequence(agent, _with_input())
    assert plan["type"] == "composite"
    assert plan["execution_logic"] == [{"type": "Sequence", "from_id": "1", "to_id": "2"}]


def test_invalid_input_is_rejected_in_strict_mode():
    agent = _make_agent({"param_validation": "strict"})
    plan = _plan_with_sequence(agent, _with_input(output="exhibits", required=False))
    assert plan["type"] == "leaf_unresolved"
    assert plan["reason"] == "Planner produced invalid action parameters."
    assert [i["issue"] for i in plan["debug"]["param_issues"]] == ["invalid_input"]

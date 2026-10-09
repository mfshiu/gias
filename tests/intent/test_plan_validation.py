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

"""LLMReplanAdvisor 單元測試（L-03）：LLM 回覆必須經過驗證才能變成 ReplanDecision。"""

from __future__ import annotations

import json

from src.core.monitoring.cursor import PlanCursor
from src.core.monitoring.events import ActionResult, EnvChange, TriggerKind
from src.core.monitoring.llm_advisor import LLMReplanAdvisor


def _plan() -> dict:
    return {
        "id": "root", "type": "composite", "intent": "去 AI 區再看展品",
        "execution_logic": [{"type": "Sequence", "from_id": "L2-1", "to_id": "L2-2"}],
        "sub_plans": [
            {"id": "L2-1", "type": "composite", "intent": "去 AI 區", "execution_logic": [], "sub_plans": [
                {"id": "a", "type": "atomic", "is_atomic": True, "task": "LocateExhibit",
                 "intent": "前往 AI 區", "params": {"target_name": "AI_Tech_Area"}, "sub_plans": []},
            ]},
            {"id": "L2-2", "type": "composite", "intent": "看展品", "execution_logic": [], "sub_plans": [
                {"id": "b", "type": "atomic", "is_atomic": True, "task": "ExplainExhibit",
                 "intent": "介紹展品", "params": {"target_name": "X"}, "sub_plans": []},
            ]},
        ],
    }


class _FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.messages = None

    def json(self, messages, schema=None):
        self.messages = messages
        return self.reply


def _cursor_with_failed_a():
    c = PlanCursor(_plan())
    tid = c.mark_dispatched("a")
    c.accept_action_result(tid, {"task_id": tid, "ok": False, "error": "unknown target"})
    return c, tid


def test_build_messages_contains_steps_failures_and_env_changes():
    c, tid = _cursor_with_failed_a()
    msgs = LLMReplanAdvisor(_FakeLLM({})).build_messages(
        intent="去 AI 區再看展品",
        env_changes=[EnvChange(topic="Zone/AI_Tech_Area/crowd_level", action="update", new_value="Crowded")],
        action_results=[ActionResult(task_id=tid, ok=False, task="LocateExhibit", error="unknown target")],
        cursor=c,
    )
    payload = json.loads(msgs[1]["content"])
    assert [s["id"] for s in payload["plan_steps"]] == ["a", "b"]
    assert payload["plan_steps"][0]["state"] == "failed"
    assert payload["failed_results"][0]["step_id"] == "a"
    assert payload["environment_changes"][0]["topic"] == "Zone/AI_Tech_Area/crowd_level"


def test_repair_with_valid_node_and_params():
    c, _ = _cursor_with_failed_a()
    d = LLMReplanAdvisor(_FakeLLM({})).to_decision(
        {"decision": "repair_node", "node_ids": ["a"], "new_params": {"target_name": "AI_Zone"}, "reason": "typo"}, c)
    assert d.kind == TriggerKind.REPAIR_NODE
    assert d.affected_node_ids == ("a",)
    assert d.new_params == {"target_name": "AI_Zone"}
    assert d.reason == "llm: typo"


def test_repair_allowed_on_pending_node_but_retry_is_not():
    c = PlanCursor(_plan())
    advisor = LLMReplanAdvisor(_FakeLLM({}))
    assert advisor.to_decision(
        {"decision": "repair_node", "node_ids": ["b"], "new_params": {"target_name": "Y"}}, c
    ).kind == TriggerKind.REPAIR_NODE
    assert advisor.to_decision({"decision": "retry_node", "node_ids": ["b"]}, c).kind == TriggerKind.NONE


def test_invalid_replies_become_none():
    c, _ = _cursor_with_failed_a()
    advisor = LLMReplanAdvisor(_FakeLLM({}))
    for reply in (
        ["not", "a", "dict"],
        {"decision": "teleport"},
        {"decision": "retry_node", "node_ids": ["ghost"]},                # 不存在的節點
        {"decision": "repair_node", "node_ids": ["a"], "new_params": {}},  # 沒有新參數
        {"decision": "replan_subtree", "node_ids": [], "subtree_root_id": "ghost"},
    ):
        assert advisor.to_decision(reply, c).kind == TriggerKind.NONE, reply


def test_replan_subtree_with_atomic_id_uses_parent_composite():
    c, _ = _cursor_with_failed_a()
    d = LLMReplanAdvisor(_FakeLLM({})).to_decision(
        {"decision": "replan_subtree", "node_ids": ["a"], "subtree_root_id": "a"}, c)
    assert d.kind == TriggerKind.REPLAN_SUBTREE
    assert d.subtree_root_id == "L2-1"


def test_replan_subtree_with_composite_id_is_kept():
    c, _ = _cursor_with_failed_a()
    d = LLMReplanAdvisor(_FakeLLM({})).to_decision(
        {"decision": "replan_subtree", "node_ids": [], "subtree_root_id": "L2-2"}, c)
    assert d.subtree_root_id == "L2-2"


def test_call_queries_llm_and_returns_validated_decision():
    c, tid = _cursor_with_failed_a()
    llm = _FakeLLM({"decision": "replan_root", "node_ids": ["a"], "reason": "area closed"})
    d = LLMReplanAdvisor(llm)(
        intent="去 AI 區再看展品", env_changes=[],
        action_results=[ActionResult(task_id=tid, ok=False)], cursor=c,
    )
    assert d.kind == TriggerKind.REPLAN_ROOT
    assert llm.messages[0]["role"] == "system"

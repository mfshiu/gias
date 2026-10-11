"""步驟之間的資料傳遞與最後回覆的單元測試（A-03）。"""

from __future__ import annotations

import copy
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.intent import dataflow as df
from src.core.intent.action_selector import ActionSelector
from src.core.intent.planner import RecursivePlanner
from src.core.intent.prompt_builder import PromptBuilder
from src.core.intention.manager import _result_summary
from src.core.intention.response import ResponseComposer, step_reports, template_response
from src.core.monitoring.cursor import NodeState, PlanCursor
from src.core.monitoring.events import ReplanDecision, TriggerKind
from src.core.monitoring.repair import PlanRepair, _renumber_ids_in_subtree
from src.kg.action_store import ActionStore, write_action_outputs

BOOTHS = {"key": "booths", "type": "list[string]", "desc": "推薦的攤位 id"}
WAYPOINTS = {"key": "waypoints", "type": "list[string]", "required": False}
DESTINATION = {"key": "destination", "type": "string", "required": True}


def _atomic(nid, task, *, outputs=(), inputs=(), params=None, schema=()):
    node = {"id": nid, "type": "atomic", "is_atomic": True, "task": task, "intent": f"do {task}",
            "params": dict(params or {}), "params_schema": list(schema), "sub_plans": []}
    if outputs:
        node["outputs"] = list(outputs)
    if inputs:
        node["inputs"] = [dict(i) for i in inputs]
    return node


def _composite(nid, children, logic=()):
    return {"id": nid, "type": "composite", "intent": nid, "sub_plans": list(children), "execution_logic": list(logic)}


def _seq(a, b):
    return {"type": "Sequence", "from_id": a, "to_id": b}


# ----------------------------------------------------------------------
# 取值
# ----------------------------------------------------------------------
@pytest.mark.parametrize("output, expected", [
    ("booths", ["B_AI1", "B_RB1"]),
    ("booths[0]", "B_AI1"),
    ("booths[-1]", "B_RB1"),
    ("route.path", ["A", "B"]),
    ("message", "推薦 2 個攤位"),
    ("booths[5]", None),          # 超出範圍
    ("empty", None),              # 空清單視為沒有產生
    ("missing", None),
    ("adaptations", None),        # 調整記錄不是輸出
])
def test_extract(output, expected):
    payload = {"message": "推薦 2 個攤位", "result": {
        "booths": ["B_AI1", "B_RB1"], "route": {"path": ["A", "B"]}, "empty": [], "adaptations": [{"kind": "x"}]}}
    assert df.extract(payload, output) == expected


# ----------------------------------------------------------------------
# 規劃後驗證
# ----------------------------------------------------------------------
def _route_plan(logic=(), *, output="booths"):
    rec = _atomic("1", "RecommendExhibits", outputs=[BOOTHS])
    route = _atomic("2", "SuggestRoute", schema=[WAYPOINTS],
                    inputs=[{"param": "waypoints", "from": "1", "output": output}])
    return _composite("root", [rec, route], logic)


def test_ordered_input_is_kept_without_new_relationship():
    plan = _route_plan([_seq("1", "2")])
    assert df.check_inputs(plan) == []
    assert plan["sub_plans"][1]["inputs"][0]["from"] == "1"


def test_unordered_input_adds_sequence():
    plan = _route_plan()
    assert df.check_inputs(plan) == [{"composite_id": "root", **_seq("1", "2")}]
    assert plan["execution_logic"] == [_seq("1", "2")]


def test_input_from_nested_step_orders_the_enclosing_branches():
    rec = _composite("A", [_atomic("A.1", "RecommendExhibits", outputs=[BOOTHS])])
    route = _composite("B", [_atomic("B.1", "SuggestRoute", schema=[WAYPOINTS],
                                     inputs=[{"param": "waypoints", "from": "A.1", "output": "booths"}])])
    plan = _composite("root", [rec, route])
    df.check_inputs(plan)
    assert plan["execution_logic"] == [_seq("A", "B")]


@pytest.mark.parametrize("plan_change, detail", [
    (lambda p: p["execution_logic"].append(_seq("2", "1")), "runs after this step"),
    (lambda p: p["sub_plans"][1]["inputs"][0].update({"from": "9"}), "step 9 not found"),
    (lambda p: p["sub_plans"][1]["inputs"][0].update({"output": "exhibits"}), "has no output 'exhibits'"),
    (lambda p: p["sub_plans"][1]["inputs"][0].update({"from": "2"}), "refers to itself"),
])
def test_invalid_inputs_are_dropped_and_recorded(plan_change, detail):
    plan = _route_plan()
    plan_change(plan)
    df.check_inputs(plan)
    route = plan["sub_plans"][1]
    assert "inputs" not in route
    [issue] = route["param_issues"]
    assert issue["issue"] == "invalid_input" and detail in issue["detail"]


def test_dropped_required_input_becomes_missing_required():
    plan = _route_plan()
    route = plan["sub_plans"][1]
    route["params_schema"] = [DESTINATION]
    route["inputs"] = [{"param": "destination", "from": "1", "output": "nothing"}]
    df.check_inputs(plan)
    assert [i["issue"] for i in route["param_issues"]] == ["invalid_input", "missing_required"]


def test_message_is_always_available_and_composite_source_is_invalid():
    plan = _route_plan(output="message")
    plan["sub_plans"][0].pop("outputs")
    df.check_inputs(plan)
    assert plan["sub_plans"][1]["inputs"]
    nested = _composite("root", [_composite("A", [_atomic("A.1", "X")]),
                                 _atomic("2", "Y", inputs=[{"param": "p", "from": "A", "output": "message"}])])
    df.check_inputs(nested)
    assert "is not an action step" in nested["sub_plans"][1]["param_issues"][0]["detail"]


# ----------------------------------------------------------------------
# 派工前代入
# ----------------------------------------------------------------------
def _lookup(state="done", payload=None):
    return lambda sid: (state, payload) if sid == "1" else (None, None)


def test_resolve_inputs_coerces_to_the_parameter_type():
    node = _atomic("2", "NavigationAssistance", schema=[DESTINATION, WAYPOINTS], params={"mode": "walk"},
                   inputs=[{"param": "destination", "from": "1", "output": "booths"},
                           {"param": "waypoints", "from": "1", "output": "booths"}])
    params, error = df.resolve_inputs(node, _lookup(payload={"result": {"booths": ["B_AI1"]}}))
    assert error is None
    assert params == {"mode": "walk", "destination": "B_AI1", "waypoints": ["B_AI1"]}


@pytest.mark.parametrize("state, payload, expected", [
    ("failed", None, "step 1 is failed"),
    ("done", {"result": {}}, "step 1 produced no 'booths'"),
])
def test_resolve_inputs_reports_unavailable_values(state, payload, expected):
    node = _atomic("2", "SuggestRoute", schema=[WAYPOINTS], inputs=[{"param": "waypoints", "from": "1", "output": "booths"}])
    params, error = df.resolve_inputs(node, _lookup(state, payload))
    assert params is None and error.startswith(expected)


def test_resolve_inputs_rejects_invalid_enum():
    node = _atomic("2", "X", schema=[{"key": "mode", "type": "enum", "enum": ["walk", "wheelchair"]}],
                   inputs=[{"param": "mode", "from": "1", "output": "message"}])
    _, error = df.resolve_inputs(node, _lookup(payload={"message": "fly"}))
    assert "is not a valid mode (invalid_enum" in error


# ----------------------------------------------------------------------
# Planner：解析 inputs、提供前面步驟的輸出給下層拆解
# ----------------------------------------------------------------------
class _Store:
    SCHEMAS = {"RecommendExhibits": [{"key": "interests", "type": "list[string]", "required": True}],
               "SuggestRoute": [{"key": "waypoints", "type": "list[string]", "required": True},
                                {"key": "current_location", "type": "string"}]}
    OUTPUTS = {"RecommendExhibits": [BOOTHS]}

    def get_action_params(self, name):
        return self.SCHEMAS.get(name, [])

    def get_action_outputs(self, name):
        return self.OUTPUTS.get(name, [])


class _KG:
    def query(self, cypher, params=None, **kw):
        name = (params or {}).get("action_name")
        return [{"action_id": f"a-{name}", "topic": "info.request", "task": name}]

    def read(self, cypher, params=None):
        return []


class _ScriptedDecomposer:
    def __init__(self, replies):
        self.replies = replies
        self.contexts = {}

    def decompose(self, intent, available_actions, context=None):
        self.contexts[intent] = context
        return self.replies[intent]


def _sub(sid, intent, action="", inputs=None):
    return {"id": sid, "intent": intent, "is_atomic": bool(action), "atomic_source": "pre_defined" if action else None,
            "action": action, "inputs": inputs or []}


def _planner(replies):
    dec = _ScriptedDecomposer(replies)
    return RecursivePlanner(decomposer=dec, logger=logging.getLogger("t"), kg=_KG(), action_store=_Store()), dec


def test_planner_turns_inputs_into_bindings():
    planner, _ = _planner({"go": {"sub_intents": [
        _sub("1", "推薦", 'RecommendExhibits(Interests=["AI"])'),
        _sub("2", "規劃路線", 'SuggestRoute(Waypoints="<from step 1>", CurrentLocation="入口")',
             inputs=[{"param": "Waypoints", "from_id": "1", "output": "booths"}]),
    ], "relationships": [_seq("1", "2")]}})
    plan = planner.plan("go", {})
    rec, route = plan["sub_plans"]
    assert rec["outputs"] == [BOOTHS]
    assert route["inputs"] == [{"param": "waypoints", "from": "1", "output": "booths"}]
    assert route["params"] == {"current_location": "入口"}          # 佔位字串不保留
    assert "param_issues" not in route                              # 必填參數由 inputs 提供


def test_planner_offers_earlier_outputs_to_nested_steps():
    planner, dec = _planner({
        "go": {"sub_intents": [_sub("1", "推薦", 'RecommendExhibits(Interests=["AI"])'), _sub("2", "帶我逛")],
               "relationships": [_seq("1", "2")]},
        "帶我逛": {"sub_intents": [_sub("1", "規劃路線", "SuggestRoute()",
                                        inputs=[{"param": "waypoints", "from_id": "@1", "output": "booths"}])],
                   "relationships": []},
    })
    plan = planner.plan("go", {})
    [offered] = dec.contexts["帶我逛"]["upstream_outputs"]
    assert (offered["id"], offered["task"], offered["outputs"]) == ("1", "RecommendExhibits", [BOOTHS])
    assert dec.contexts["go"] is None
    assert plan["sub_plans"][1]["sub_plans"][0]["inputs"] == [{"param": "waypoints", "from": "1", "output": "booths"}]


def test_planner_does_not_offer_outputs_of_later_steps():
    planner, dec = _planner({
        "go": {"sub_intents": [_sub("1", "推薦", "RecommendExhibits()"), _sub("2", "帶我逛")],
               "relationships": [_seq("2", "1")]},
        "帶我逛": {"sub_intents": [_sub("1", "規劃路線", "SuggestRoute()")], "relationships": []},
    })
    planner.plan("go", {})
    assert dec.contexts["帶我逛"] is None


def test_planner_records_unknown_input_parameter():
    planner, _ = _planner({"go": {"sub_intents": [
        _sub("1", "推薦", "RecommendExhibits()"),
        _sub("2", "規劃路線", "SuggestRoute()", inputs=[{"param": "Stops", "from_id": "1", "output": "booths"}]),
    ], "relationships": []}})
    route = planner.plan("go", {})["sub_plans"][1]
    assert "inputs" not in route
    assert {i["issue"] for i in route["param_issues"]} == {"invalid_input", "missing_required"}


# ----------------------------------------------------------------------
# Prompt 與動作清單
# ----------------------------------------------------------------------
def test_prompt_lists_upstream_outputs_and_completed_results():
    ctx = {
        "upstream_outputs": [{"id": "1", "task": "RecommendExhibits", "intent": "推薦", "outputs": [BOOTHS]}],
        "replan": {"reason": "r", "affected_steps": []},
        "completed_steps": [{"task": "RecommendExhibits", "params": {}, "message": "推薦 B_AI1",
                             "outputs": {"booths": ["B_AI1"]}}],
    }
    prompt = PromptBuilder().build_prompt("x", {"A()": "a"}, ctx)
    assert "- @1 RecommendExhibits (推薦): booths (list[string]): 推薦的攤位 id; message: text reply" in prompt
    assert '- **Completed step**: RecommendExhibits {} -> 推薦 B_AI1; outputs {"booths": ["B_AI1"]}' in prompt
    assert "11. **Reuse Completed Results**" in prompt


def test_selector_lists_outputs_in_action_descriptions():
    class KG:
        def query(self, cypher, params=None, **kw):
            if "HAS_OUTPUT" in cypher:
                return [{"action": "RecommendExhibits", **BOOTHS}]
            return [{"action": name, "key": "interests", "order": 0} for name in params["names"]]

    sel = ActionSelector(kg=KG(), matcher=None, logger=logging.getLogger("t"))
    match = lambda name, desc: SimpleNamespace(action=SimpleNamespace(name=name, description=desc))
    out = sel._to_prompt_format([match("RecommendExhibits", "推薦展品"), match("AnswerFAQ", "回答問題")])
    assert out == {
        "RecommendExhibits(Interests)": "推薦展品 | Outputs: booths (list[string]): 推薦的攤位 id; message: text reply",
        "AnswerFAQ(Interests)": "回答問題",
    }


def test_action_store_reads_and_writes_outputs():
    class KG:
        def __init__(self):
            self.writes = []

        def query(self, cypher, params=None, **kw):
            return [BOOTHS]

        def write(self, cypher, params=None):
            self.writes.append(params)

    kg = KG()
    assert ActionStore(kg).get_action_outputs("RecommendExhibits") == [BOOTHS]
    write_action_outputs(kg.write, "a1", [{"key": "booths", "type": "list[string]"}, {"key": "exhibitors"}])
    assert [(w["key"], w["order"], w["type"]) for w in kg.writes] == [
        ("booths", 0, "list[string]"), ("exhibitors", 1, "string")]


# ----------------------------------------------------------------------
# Cursor 與重規劃
# ----------------------------------------------------------------------
def test_cursor_waits_for_input_source_without_sequence():
    plan = _route_plan()                     # 沒有 Sequence：靠 inputs 等待
    cursor = PlanCursor(plan)
    assert [r.node_id for r in cursor.next_ready_atomics()] == ["1"]
    tid = cursor.mark_dispatched("1")
    assert cursor.next_ready_atomics() == []
    cursor.accept_action_result(tid, {"task_id": tid, "ok": True, "result": {"booths": ["B_AI1"]}})
    assert [r.node_id for r in cursor.next_ready_atomics()] == ["2"]


def test_replan_context_includes_completed_results_and_renumbers_inputs():
    cursor = PlanCursor(_route_plan([_seq("1", "2")]))
    tid = cursor.mark_dispatched("1")
    cursor.accept_action_result(tid, {"task_id": tid, "ok": True, "message": "推薦 B_AI1",
                                      "result": {"booths": ["B_AI1"], "adaptations": []}})
    decision = ReplanDecision(kind=TriggerKind.REPLAN_SUBTREE, reason="x", affected_node_ids=["2"])
    ctx = PlanRepair(logger=logging.getLogger("t")).build_replan_context(decision, cursor)
    [done] = ctx["completed_steps"]
    assert (done["id"], done["message"], done["outputs"]) == ("1", "推薦 B_AI1", {"booths": ["B_AI1"]})

    new = _route_plan([_seq("1", "2")])
    _renumber_ids_in_subtree(new, prefix="r1")
    assert new["sub_plans"][1]["inputs"][0]["from"] == "r1-1"


# ----------------------------------------------------------------------
# 執行端的輸出
# ----------------------------------------------------------------------
def test_info_agent_returns_recommended_booths():
    from src.agents.info_agent import _execute

    booths = [{"id": "B_AI1", "exhibitor": "TechCorp"}, {"id": "B_RB1", "exhibitor": "RoboInc"}]
    outputs = {}
    with patch("src.agents.info_agent.time.sleep"), \
         patch("src.agents.info_agent.get_crowd_hotspots", return_value=[{"zone": "VR_Area", "crowd_status": "Crowded"}]), \
         patch("src.agents.info_agent.get_open_booths", return_value=booths):
        message, cancelled = _execute(None, "RecommendExhibits", {"interests": ["AI"], "limit": 1}, outputs=outputs)
    assert not cancelled
    assert outputs == {"booths": ["B_AI1"], "exhibitors": ["TechCorp"], "avoid_zones": ["VR_Area"]}


def test_guide_agent_reports_where_it_arrived():
    from navigation_scenario.live.guide_agent import GuideAgent
    from navigation_scenario.pathing import snapshot_from_config

    snap = snapshot_from_config()
    guide = GuideAgent({"broker": {"broker_name": "mqtt01", "mqtt01": {"broker_type": "empty"}}},
                       snapshot_loader=lambda: snap, seconds_per_meter=0.0)
    outputs = {}
    guide.execute_task("LocateExhibit", {"target_name": "AI_Tech_Area"}, outputs=outputs)
    assert outputs == {"reached": ["B_AI1"], "current_location": "B_AI1"}


# ----------------------------------------------------------------------
# 最後的回覆
# ----------------------------------------------------------------------
SUMMARY = {"ok": False, "results": [
    {"id": "1", "intent": "推薦 AI 展品", "task": "RecommendExhibits", "state": "done",
     "result": {"ok": True, "message": "推薦 B_AI1", "result": {"booths": ["B_AI1"]}}},
    {"id": "2", "intent": "舊的路線", "task": "SuggestRoute", "state": "obsolete", "error": "replaced"},
    {"id": "3", "intent": "帶我過去", "task": "LocateExhibit", "state": "failed", "error": "route blocked",
     "adaptations": [{"kind": "reroute", "reason": "crowd changed"}]},
]}


def test_step_reports_skip_replaced_steps():
    assert step_reports(SUMMARY) == [
        {"intent": "推薦 AI 展品", "task": "RecommendExhibits", "state": "done", "message": "推薦 B_AI1",
         "outputs": {"booths": ["B_AI1"]}},
        {"intent": "帶我過去", "task": "LocateExhibit", "state": "failed", "message": "", "error": "route blocked",
         "adaptations": ["crowd changed"]},
    ]


def test_template_response():
    steps = step_reports(SUMMARY)
    assert template_response("逛 AI 展區", ok=False, steps=steps) == (
        "「逛 AI 展區」未能全部完成。\n・推薦 AI 展品：推薦 B_AI1\n・帶我過去：未完成（route blocked）")
    assert template_response("x", ok=True, steps=steps[:1]).startswith("已完成「x」。")
    assert template_response("x", ok=False, steps=[], reason="out of scope") == "抱歉，無法處理「x」：out of scope"


class _LLM:
    def __init__(self, content="", exc=None):
        self.content, self.exc, self.prompts = content, exc, []

    def chat(self, messages, **kw):
        self.prompts.append(messages[-1]["content"])
        if self.exc:
            raise self.exc
        return SimpleNamespace(content=self.content)


def test_llm_reply_uses_step_results():
    llm = _LLM("已為您推薦 B_AI1，但前往的路線目前受阻。")
    reply = ResponseComposer(llm).compose("逛 AI 展區", plan={"type": "composite"}, summary=SUMMARY)
    assert reply == {"text": "已為您推薦 B_AI1，但前往的路線目前受阻。", "source": "llm"}
    assert "Outcome: not fully completed" in llm.prompts[0] and '"booths": ["B_AI1"]' in llm.prompts[0]


@pytest.mark.parametrize("llm", [_LLM("  "), _LLM(exc=RuntimeError("401"))])
def test_llm_failure_falls_back_to_template(llm):
    reply = ResponseComposer(llm).compose("逛 AI 展區", plan={"type": "composite"}, summary=SUMMARY)
    assert reply["source"] == "template" and reply["text"].startswith("「逛 AI 展區」未能全部完成")


def test_unresolved_plan_and_off_mode():
    llm = _LLM("抱歉，目前無法處理。")
    plan = {"type": "leaf_unresolved", "reason": "Plan violates action preconditions."}
    assert ResponseComposer(llm).compose("x", plan=plan)["text"] == "抱歉，目前無法處理。"
    assert "could not be planned: Plan violates action preconditions." in llm.prompts[0]
    assert ResponseComposer(llm, mode="template").compose("x", plan=plan)["source"] == "template"
    assert ResponseComposer(llm, mode="off").compose("x", plan=plan) is None


def test_intention_result_keeps_reply_text():
    result = _result_summary({"ok": True, "message": "執行完成。", "response": {"text": "好了", "source": "llm"}})
    assert result["response"] == "好了"
    assert _result_summary({"ok": True})["response"] is None

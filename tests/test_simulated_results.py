"""
執行端的「模擬」標記（V-06）

- 執行端逐個 task 聲明結果是否為模擬：內建的 InfoAgent / NavigationAgent 是，GuideAgent 的移動類 task 不是
- IntentionalAgent 在執行摘要與意圖結果列出模擬完成的步驟（simulated_steps），ok 的定義不變
- 最後回覆註明哪些步驟只是模擬
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from navigation_scenario.live.guide_agent import GuideAgent
from navigation_scenario.pathing import snapshot_from_config
from src.agents._executor_utils import build_result
from src.agents.info_agent import InfoAgent
from src.agents.navigation_agent import NavigationAgent
from src.core.intention.manager import _result_summary
from src.core.intention.response import ResponseComposer, step_reports, template_response
from src.core.intentional_agent import IntentionalAgent
from src.core.monitoring import PlanCursor
from src.core.monitoring.events import is_simulated_result

_EMPTY_BROKER = {"broker": {"broker_name": "mqtt01", "mqtt01": {"broker_type": "empty"}}}


class _Parcel:
    def __init__(self, content):
        self.content = content


@pytest.fixture
def fast_executors():
    """內建執行端不等待、不查 Blackboard。"""
    with patch("src.agents.info_agent.time.sleep", lambda *a, **k: None), \
         patch("src.agents.navigation_agent.time.sleep", lambda *a, **k: None), \
         patch("src.agents.navigation_agent.get_crowd_hotspots", return_value=[]):
        yield


def _handle(agent, topic, content):
    with patch.object(agent, "publish"):
        return agent._handle(topic, _Parcel(content))


# ----------------------------------------------------------------------
# 執行端
# ----------------------------------------------------------------------
def test_build_result_is_not_simulated_by_default():
    assert build_result("T", "m", None, "")["simulated"] is False
    assert build_result("T", "m", None, "", simulated=True)["simulated"] is True


def test_builtin_executors_mark_results_simulated(fast_executors):
    info = _handle(InfoAgent(_EMPTY_BROKER), "info.request",
                   {"task": "AnswerFAQ", "task_id": "t1", "params": {"question": "幾點開門"}})
    nav = _handle(NavigationAgent(_EMPTY_BROKER), "navigation.request",
                  {"task": "ExplainDirections", "task_id": "t2", "params": {"destination": "VR_Area"}})
    assert info["ok"] is True and info["simulated"] is True
    assert nav["ok"] is True and nav["simulated"] is True


@pytest.mark.parametrize("agent_cls, topic", [(InfoAgent, "info.request"), (NavigationAgent, "navigation.request")])
def test_rejected_request_is_not_simulated(agent_cls, topic):
    """缺少 task 的請求沒有執行任何事，也不是模擬。"""
    result = _handle(agent_cls(_EMPTY_BROKER), topic, {"task_id": "t1", "params": {}})
    assert result["ok"] is False and result["simulated"] is False


def test_guide_agent_marks_only_non_move_tasks_simulated(fast_executors):
    guide = GuideAgent(_EMPTY_BROKER, snapshot_loader=snapshot_from_config, seconds_per_meter=0.0)

    moved = _handle(guide, "navigation.request",
                    {"task": "LocateExhibit", "task_id": "t1", "params": {"target_name": "B_AI1"}})
    told = _handle(guide, "navigation.request",
                   {"task": "ExplainDirections", "task_id": "t2", "params": {"destination": "VR_Area"}})

    assert moved["ok"] is True and moved["simulated"] is False
    assert moved["result"]["current_location"] == "B_AI1"
    assert told["ok"] is True and told["simulated"] is True


def test_is_simulated_result_requires_true():
    assert is_simulated_result({"simulated": True})
    for payload in ({"simulated": False}, {"simulated": "yes"}, {}, None, "simulated"):
        assert not is_simulated_result(payload)


# ----------------------------------------------------------------------
# 執行摘要
# ----------------------------------------------------------------------
def _atomic(node_id, task="T", topic="info.request"):
    return {"id": node_id, "type": "atomic", "is_atomic": True, "task": task, "topic": topic,
            "intent": f"step {node_id}", "params": {}, "sub_plans": []}


def _plan(*nodes):
    return {"id": "root", "type": "composite", "intent": "root", "sub_plans": list(nodes), "execution_logic": []}


def _run_cursor(plan, results):
    cursor = PlanCursor(plan)
    for rec in cursor.next_ready_atomics():
        tid = "t-" + rec.node_id
        cursor.mark_dispatched(rec.node_id, task_id=tid)
        cursor.accept_action_result(tid, results[rec.node_id])
    return cursor.summary()


def test_cursor_summary_lists_simulated_done_steps():
    summary = _run_cursor(_plan(_atomic("a"), _atomic("b"), _atomic("c")), {
        "a": {"ok": True, "simulated": True},
        "b": {"ok": True, "simulated": False},
        "c": {"ok": True},
    })
    assert summary["ok"] is True                      # ok 的定義不變
    assert summary["simulated_steps"] == ["a"]
    flags = {r["id"]: r["simulated"] for r in summary["results"]}
    assert flags == {"a": True, "b": False, "c": False}


def test_failed_simulated_step_is_not_listed():
    summary = _run_cursor(_plan(_atomic("a"), _atomic("b")), {
        "a": {"ok": False, "simulated": True, "error": "boom"},
        "b": {"ok": True, "simulated": True},
    })
    assert summary["ok"] is False
    assert summary["simulated_steps"] == ["b"]


def _make_agent() -> IntentionalAgent:
    cfg = {
        "llm": {"provider": "mock"},
        "broker": {"broker_name": "mqtt01"},
        "broker.mqtt01": {"broker_type": "mqtt", "host": "localhost", "port": 1883},
        "kg": {"type": "neo4j", "neo4j": {"uri": "bolt://localhost:7687"}, "neo4j_actions": {}},
        "intent": {"monitoring": {"max_replans": 1, "max_retries_per_node": 0, "poll_interval_sec": 0.02,
                                  "enable_llm_assist": False}},
    }
    with patch("src.core.intentional_agent.Neo4jBoltAdapter") as adapter_cls:
        adapter_cls.from_config.return_value = MagicMock()
        return IntentionalAgent(agent_config=cfg, intention="test")


def test_sync_execute_plan_lists_simulated_steps():
    agent = _make_agent()
    replies = {
        "a": {"ok": True, "simulated": True},
        "b": {"ok": True, "simulated": False},
        "c": {"ok": False, "simulated": True, "error": "boom"},
    }

    def publish_sync(topic, payload, timeout=30):
        return SimpleNamespace(content=dict(replies[payload["action_id"]]), error=None)

    plan = _plan(*({**_atomic(n), "action_id": n} for n in "abc"))
    with patch.object(agent, "publish_sync", side_effect=publish_sync):
        summary = agent.execute_plan(plan)
    assert summary["ok"] is False
    assert summary["simulated_steps"] == ["a"]       # 失敗的步驟不列入


def test_monitoring_loop_with_real_executors(fast_executors):
    """派工給真正的執行端：InfoAgent 的回答是模擬，GuideAgent 的移動不是。"""
    agent = _make_agent()
    info = InfoAgent(_EMPTY_BROKER)
    guide = GuideAgent(_EMPTY_BROKER, snapshot_loader=snapshot_from_config, seconds_per_meter=0.0)
    executors = {"info.request": info, "navigation.request": guide}

    def dispatcher(node, payload):
        if node.get("_cancel"):
            return
        topic = node["topic"]
        result = _handle(executors[topic], topic, payload)
        agent._monitor.on_action_result(topic.replace(".request", ".result"), result)

    plan = _plan(
        {**_atomic("faq", task="AnswerFAQ"), "params": {"question": "幾點開門"}},
        {**_atomic("go", task="LocateExhibit", topic="navigation.request"), "params": {"target_name": "B_AI1"}},
    )
    summary = agent.execute_plan_with_monitoring(plan, dispatcher=dispatcher)

    assert summary["ok"] is True
    assert summary["simulated_steps"] == ["faq"]
    assert _result_summary(summary)["simulated_steps"] == ["faq"]

    reply = ResponseComposer(mode="template").compose("問開門時間再帶我去 AI 展區", plan=plan, summary=summary)
    lines = reply["text"].splitlines()
    assert next(l for l in lines if l.startswith("・step faq")).endswith("（模擬執行）")
    assert not next(l for l in lines if l.startswith("・step go")).endswith("（模擬執行）")
    assert lines[-1] == "註：標示「模擬執行」的步驟由示範用的執行端回覆，並未實際完成。"


def test_intention_result_defaults_to_no_simulated_steps():
    assert _result_summary({"ok": True})["simulated_steps"] == []


# ----------------------------------------------------------------------
# 最後回覆
# ----------------------------------------------------------------------
SUMMARY = {"ok": True, "results": [
    {"id": "1", "intent": "查詢開放時間", "task": "AnswerFAQ", "state": "done",
     "result": {"ok": True, "message": "已回覆 FAQ", "simulated": True}},
    {"id": "2", "intent": "帶我過去", "task": "LocateExhibit", "state": "done",
     "result": {"ok": True, "message": "已抵達 B_AI1", "simulated": False}},
    {"id": "3", "intent": "介紹展品", "task": "ExplainExhibit", "state": "failed", "error": "boom",
     "result": {"ok": False, "simulated": True}},
]}


def test_step_reports_mark_only_done_simulated_steps():
    steps = step_reports(SUMMARY)
    assert [s.get("simulated") for s in steps] == [True, None, None]


def test_template_without_simulated_steps_is_unchanged():
    steps = [{"intent": "帶我過去", "state": "done", "message": "已抵達"}]
    assert template_response("x", ok=True, steps=steps) == "已完成「x」。\n・帶我過去：已抵達"


class _LLM:
    def __init__(self):
        self.prompts = []

    def chat(self, messages, **kw):
        self.prompts.append(messages[-1]["content"])
        return SimpleNamespace(content="好的")


def test_llm_prompt_mentions_simulated_steps_only_when_present():
    llm = _LLM()
    ResponseComposer(llm).compose("x", plan={"type": "composite"}, summary=SUMMARY)
    assert "Outcome: completed; some steps were only simulated" in llm.prompts[0]
    assert 'Steps marked "simulated": true were not actually carried out' in llm.prompts[0]

    real = {"ok": True, "results": [SUMMARY["results"][1]]}
    ResponseComposer(llm).compose("x", plan={"type": "composite"}, summary=real)
    assert "Outcome: completed\n" in llm.prompts[1]
    assert "simulated" not in llm.prompts[1]

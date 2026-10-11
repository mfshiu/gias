"""IntentionalAgent 意圖服務整合測試（A-01）。

LLM 規劃以預備計畫取代、派工以注入的 dispatcher 取代，其餘（IntentionManager、監測迴圈）為真實程式。
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.app_helper import get_agent_config
from src.core.intention import BlackboardIntentionStore, InMemoryIntentionStore
from src.core.intentional_agent import IntentionalAgent


class _Parcel:
    def __init__(self, content):
        self.content = content


def _config(store="memory") -> dict:
    try:
        cfg = dict(get_agent_config())
        if not (cfg.get("llm") and cfg.get("broker")):
            raise ValueError
    except Exception:
        cfg = {"llm": {"provider": "mock"}, "broker": {"broker_name": "mqtt01"},
               "kg": {"type": "neo4j", "neo4j": {"uri": "bolt://localhost:7687"}, "neo4j_actions": {}}}
    intent = dict(cfg.get("intent") or {})
    intent["monitoring"] = {"poll_interval_sec": 0.01, "max_replans": 1, "max_retries_per_node": 1,
                            "enable_llm_assist": False}
    intent["intentions"] = {"store": store}
    cfg["intent"] = intent
    return cfg


def _plan(text, *tasks):
    nodes = [{"id": str(i), "type": "atomic", "is_atomic": True, "intent": f"{text}-{t}", "task": t,
              "topic": "info.request", "params": {}, "sub_plans": []} for i, t in enumerate(tasks, 1)]
    seq = [{"type": "Sequence", "from_id": str(i), "to_id": str(i + 1)} for i in range(1, len(nodes))]
    return {"id": "root", "type": "composite", "intent": text, "sub_plans": nodes, "execution_logic": seq}


class _Harness:
    def __init__(self, intention=None, store="memory", plans=None):
        with patch("src.core.intentional_agent.Neo4jBoltAdapter") as adapter_cls:
            adapter_cls.from_config.return_value = MagicMock()
            self.agent = IntentionalAgent(_config(store), intention)
        self.handlers: dict = {}
        self.published: list[tuple[str, dict]] = []
        self.contexts: list[tuple[str, dict | None]] = []
        self.plans = plans or {}
        self.slow_tasks: dict[str, threading.Event] = {}
        self.cancelled: list[str] = []
        self.terminated = threading.Event()
        a = self.agent
        a.subscribe = lambda topic, data_type="str", topic_handler=None: self.handlers.__setitem__(topic, topic_handler)
        a.publish = lambda topic, data=None: self.published.append((topic, data))
        a._terminate = self.terminated.set
        a.plan_intention = self._plan_intention
        a.execution_dispatcher = self._dispatch

    def _plan_intention(self, text, *, context=None):
        self.contexts.append((text, context))
        return self.plans[text]() if callable(self.plans.get(text)) else self.plans.get(text, _plan(text, "T"))

    def _dispatch(self, node, payload):
        mon = self.agent._monitor
        if node.get("_cancel"):
            self.cancelled.append(payload["task_id"])
            mon.on_action_result("info.result", {"task_id": payload["task_id"], "ok": False, "cancelled": True})
            return
        gate = self.slow_tasks.get(payload["task"])
        if gate is None:
            mon.on_action_result("info.result", {"task_id": payload["task_id"], "ok": True})
            return

        def finish():
            if gate.wait(5):
                mon.on_action_result("info.result", {"task_id": payload["task_id"], "ok": True})

        threading.Thread(target=finish, daemon=True).start()

    def statuses(self, intention_id):
        return [d["status"] for t, d in self.published if t == "intention.status" and d["intention_id"] == intention_id]

    def close(self):
        for gate in self.slow_tasks.values():
            gate.set()
        self.agent.on_terminating()


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def test_one_shot_mode_processes_intention_then_terminates():
    """既有啟動腳本的行為：給 intention → 處理完就結束，不開放 MQTT 意圖介面。"""
    h = _Harness(intention="帶我去 AI 展區")
    h.agent.on_activate()
    assert h.terminated.is_set()
    [item] = h.agent.intentions.list()
    assert item["status"] == "achieved"
    assert h.statuses(item["intention_id"]) == ["pending", "planning", "executing", "achieved"]
    assert not any(t.startswith("intention.") for t in h.handlers)
    h.close()


def test_service_mode_mqtt_submit_query_cancel():
    h = _Harness(plans={"慢": _plan("慢", "Slow")})
    h.slow_tasks["Slow"] = threading.Event()
    h.agent.on_activate()
    assert not h.terminated.is_set()
    assert {"intention.submit", "intention.cancel", "intention.query"} <= set(h.handlers)

    r1 = h.handlers["intention.submit"]("intention.submit", _Parcel({"text": "慢", "requester": "kiosk-1"}))
    r2 = h.handlers["intention.submit"]("intention.submit", _Parcel({"text": "介紹展品", "priority": 0}))
    assert r1["ok"] and r2["ok"]
    assert h.handlers["intention.submit"]("intention.submit", _Parcel({"text": ""}))["ok"] is False

    q = h.handlers["intention.query"]("intention.query", _Parcel({"intention_id": r2["intention_id"]}))
    assert q["intention"]["status"] == "pending"
    c = h.handlers["intention.cancel"]("intention.cancel", _Parcel({"intention_id": r2["intention_id"]}))
    assert c["ok"] is True

    _wait(lambda: h.agent.intentions.get(r1["intention_id"])["status"] == "executing")
    h.slow_tasks["Slow"].set()
    assert h.agent.intentions.wait_idle(5)
    listing = h.handlers["intention.query"]("intention.query", _Parcel({}))["intentions"]
    assert {i["intention_id"]: i["status"] for i in listing} == {
        r1["intention_id"]: "achieved", r2["intention_id"]: "dropped"}
    assert listing[0]["requester"] == "kiosk-1"
    h.close()


def test_preemption_cancels_running_step_and_resume_skips_completed_steps():
    """低優先序意圖執行到第二步時被搶占：取消執行中的動作；續做時告訴規劃者第一步已完成。"""
    h = _Harness(plans={"參觀": _plan("參觀", "Done1", "Slow"), "緊急": _plan("緊急", "Help")})
    h.slow_tasks["Slow"] = threading.Event()
    h.agent.on_activate()
    low = h.agent.intentions.submit("參觀", priority=0)
    _wait(lambda: h.agent._monitor is not None and len(h.cancelled) == 0
          and h.agent.intentions.get(low.intention_id)["status"] == "executing")
    time.sleep(0.1)   # 讓第一步完成、第二步派工
    high = h.agent.intentions.submit("緊急", priority=9)
    _wait(lambda: h.agent.intentions.get(high.intention_id)["status"] == "achieved")
    assert len(h.cancelled) == 1                          # 被搶占時取消了執行中的 Slow
    _wait(lambda: h.agent.intentions.get(low.intention_id)["status"] == "executing"
          and h.agent.intentions.get(low.intention_id)["attempts"] == 2)
    h.slow_tasks["Slow"].set()
    assert h.agent.intentions.wait_idle(5)

    assert h.statuses(low.intention_id) == [
        "pending", "planning", "executing", "suspended", "planning", "executing", "achieved"]
    resume_text, resume_ctx = h.contexts[-1]
    assert resume_text == "參觀"
    assert resume_ctx["replan"]["kind"] == "resume"
    assert [s["task"] for s in resume_ctx["replan"]["affected_steps"]] == ["Done1"]
    h.close()


def test_stop_event_stops_monitored_execution():
    h = _Harness()
    h.slow_tasks["Slow"] = threading.Event()
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    summary = h.agent.execute_plan_with_monitoring(_plan("x", "Slow"), dispatcher=h._dispatch, stop_event=stop)
    assert summary["stopped"] is True and summary["ok"] is False
    assert len(h.cancelled) == 1
    h.close()


def test_blackboard_store_in_service_mode_marks_previous_unfinished():
    writes = []
    with patch("src.core.intentional_agent.write_blackboard", lambda agent, c, p=None, **kw: writes.append(c) or True):
        h = _Harness(store="blackboard")
        h.agent.on_activate()
        assert isinstance(h.agent.intentions.store, BlackboardIntentionStore)
        it = h.agent.intentions.submit("x")
        assert h.agent.intentions.wait_idle(5)
        assert h.agent.intentions.store.flush(2)
        h.close()
    assert "SET i.status = 'interrupted'" in writes[0]          # 啟動時先處理上次未完成的意圖
    assert sum("MERGE (i:Intention" in c for c in writes) >= 4  # pending / planning / executing / achieved


def test_one_shot_mode_does_not_touch_previous_intentions():
    writes = []
    with patch("src.core.intentional_agent.write_blackboard", lambda agent, c, p=None, **kw: writes.append(c) or True):
        h = _Harness(intention="x", store="blackboard")
        h.agent.on_activate()
        h.agent.intentions.store.flush(2)
        h.close()
    assert not any("interrupted" in c for c in writes)


def test_memory_store_can_be_selected():
    h = _Harness(store="memory")
    h.agent.on_activate()
    assert isinstance(h.agent.intentions.store, InMemoryIntentionStore)
    h.close()

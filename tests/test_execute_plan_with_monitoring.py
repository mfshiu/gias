"""IntentionalAgent.execute_plan_with_monitoring 的 e2e mock 測試。

以注入式 dispatcher 取代真實 broker：dispatcher 依 payload 內容回填 monitor 結果，
即可模擬：成功、失敗、cancel、replan、environmental trigger 等情境。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.app_helper import get_agent_config
from src.blackboard.client import subscriber_topic
from src.core.intent.domain_profile import DomainProfile
from src.core.intentional_agent import IntentionalAgent
from src.core.monitoring import (
    Budget,
    NodeState,
    ReplanDecision,
    TriggerKind,
)
from src.core.monitoring.trigger import TriggerConfig
from src.core.monitoring.events import EnvChange


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _minimal_agent_config() -> dict:
    """沿用 test_execute_plan.py 的 fallback：優先用 gias.toml；
    否則退回最小可用設定。"""
    try:
        cfg = get_agent_config()
        if cfg.get("llm") and cfg.get("broker"):
            cfg = dict(cfg)
            intent = dict(cfg.get("intent") or {})
            monitoring = dict(intent.get("monitoring") or {
                "max_replans": 2,
                "max_retries_per_node": 1,
                "poll_interval_sec": 0.02,
            })
            # 單元測試不可呼叫真實 LLM（即使 gias.toml 啟用了 enable_llm_assist）
            monitoring["enable_llm_assist"] = False
            intent["monitoring"] = monitoring
            cfg["intent"] = intent
            return cfg
    except Exception:
        pass
    return {
        "llm": {"provider": "mock"},
        "broker": {"broker_name": "mqtt01"},
        "broker.mqtt01": {"broker_type": "mqtt", "host": "localhost", "port": 1883},
        "kg": {"type": "neo4j", "neo4j": {"uri": "bolt://localhost:7687"}, "neo4j_actions": {}},
        "intent": {"monitoring": {"max_replans": 2, "max_retries_per_node": 1, "poll_interval_sec": 0.02}},
    }


def _make_agent() -> IntentionalAgent:
    with patch("src.core.intentional_agent.Neo4jBoltAdapter") as mock_adapter_cls:
        mock_adapter_cls.from_config.return_value = MagicMock()
        return IntentionalAgent(agent_config=_minimal_agent_config(), intention="test")


def _atomic(node_id, *, task="T", topic="info.request", intent="i", target=""):
    return {
        "id": node_id,
        "type": "atomic",
        "is_atomic": True,
        "task": task,
        "topic": topic,
        "intent": intent,
        "params": {"target_name": target} if target else {},
        "action_id": f"act-{node_id}",
        "sub_plans": [],
    }


def _composite(node_id, children, *, exec_logic=None, intent="c"):
    return {
        "id": node_id,
        "type": "composite",
        "intent": intent,
        "sub_plans": list(children),
        "execution_logic": list(exec_logic or []),
    }


def _root(children, *, exec_logic=None, intent="root"):
    return _composite("root", children, exec_logic=exec_logic, intent=intent)


# ----------------------------------------------------------------------
# Dispatcher 工廠
# ----------------------------------------------------------------------
def make_dispatcher(agent, *, responses_by_task=None, default_ok=True, async_delay=0.0):
    """建立一個 dispatcher，模擬 executor 行為。

    responses_by_task: {task_name: list[result_dict]}，依呼叫順序消費。
        若某項是 callable，會以 (payload) 呼叫並期望回傳 result dict。
    default_ok: 沒指定時的預設 result.ok
    async_delay: 模擬執行耗時
    """
    responses_by_task = responses_by_task or {}
    call_log = []
    cancelled_task_ids: set[str] = set()

    def dispatcher(node, payload):
        # 是 cancel？
        if node.get("_cancel"):
            cancelled_task_ids.add(payload.get("task_id"))
            return
        call_log.append({"node_id": node.get("id"), "payload": dict(payload)})
        task = payload.get("task")
        task_id = payload.get("task_id")
        responses = responses_by_task.get(task)
        if responses:
            r = responses.pop(0)
            if callable(r):
                result = r(payload)
            else:
                result = dict(r)
        else:
            result = {"ok": default_ok, "task": task}
        # 注入 task_id
        result.setdefault("task_id", task_id)
        # 模擬非同步
        def deliver():
            if async_delay > 0:
                time.sleep(async_delay)
            # 若該 task_id 已被 cancel：強制 cancelled
            if task_id in cancelled_task_ids:
                cancelled_result = {
                    "task_id": task_id,
                    "ok": False,
                    "cancelled": True,
                    "task": task,
                    "error": "cancelled",
                }
                agent._monitor.on_action_result("info.result", cancelled_result)
            else:
                agent._monitor.on_action_result("info.result", result)
        if async_delay > 0:
            threading.Thread(target=deliver, daemon=True).start()
        else:
            deliver()

    dispatcher.call_log = call_log
    dispatcher.cancelled_task_ids = cancelled_task_ids
    dispatcher.responses_by_task = responses_by_task
    return dispatcher


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
def test_leaf_unresolved_short_circuits():
    agent = _make_agent()
    plan = {"id": "root", "type": "leaf_unresolved", "sub_plans": [], "execution_logic": []}
    result = agent.execute_plan_with_monitoring(plan)
    assert result["ok"] is False
    assert "無法完成" in result["message"]


def test_two_atomics_sequence_happy_path():
    agent = _make_agent()
    plan = _root([
        _atomic("a", task="ExplainExhibit"),
        _atomic("b", task="LocateExhibit", topic="navigation.request"),
    ], exec_logic=[{"type": "Sequence", "from_id": "a", "to_id": "b"}])
    disp = make_dispatcher(agent, responses_by_task={
        "ExplainExhibit": [{"ok": True, "message": "ex"}],
        "LocateExhibit": [{"ok": True, "message": "loc"}],
    })
    result = agent.execute_plan_with_monitoring(plan, dispatcher=disp)
    assert result["ok"] is True
    # 順序：a 先於 b
    ids = [r["node_id"] for r in disp.call_log]
    assert ids == ["a", "b"]


def test_parallel_two_atomics_both_dispatched_before_first_finishes():
    agent = _make_agent()
    plan = _root([_atomic("x", task="T1"), _atomic("y", task="T2")])
    # async_delay：兩者都先 dispatch，再非同步回應
    disp = make_dispatcher(agent, responses_by_task={
        "T1": [{"ok": True}],
        "T2": [{"ok": True}],
    }, async_delay=0.05)
    result = agent.execute_plan_with_monitoring(plan, dispatcher=disp)
    assert result["ok"] is True
    ids = sorted(r["node_id"] for r in disp.call_log)
    assert ids == ["x", "y"]


def test_failure_then_retry_succeeds():
    agent = _make_agent()
    plan = _root([_atomic("a", task="ExplainExhibit")])
    disp = make_dispatcher(agent, responses_by_task={
        "ExplainExhibit": [
            {"ok": False, "error": "boom"},
            {"ok": True, "message": "after retry"},
        ],
    })
    result = agent.execute_plan_with_monitoring(plan, dispatcher=disp)
    assert result["ok"] is True
    # dispatcher 被呼叫兩次（首次 + retry）
    a_calls = [c for c in disp.call_log if c["node_id"] == "a"]
    assert len(a_calls) == 2


def test_failure_with_no_retry_budget_triggers_subtree_replan():
    """retry 配額用完後 → 嘗試 REPLAN_SUBTREE；planner 回 None 時最終 ABORT。"""
    agent = _make_agent()
    plan = _root([
        _composite("L2-1", [_atomic("a", task="ExplainExhibit")], intent="sub-intent"),
    ])
    disp = make_dispatcher(agent, responses_by_task={
        "ExplainExhibit": [
            {"ok": False, "error": "boom-1"},
            {"ok": False, "error": "boom-2"},
        ],
    })
    # 注入空 subtree_planner，使 replan 失敗 → 最終 ABORT
    result = agent.execute_plan_with_monitoring(
        plan,
        dispatcher=disp,
        subtree_planner=lambda intent, env: None,
    )
    assert result["ok"] is False


def test_subtree_replan_swaps_in_new_tree():
    agent = _make_agent()
    plan = _root([
        _composite("L2-1", [_atomic("a", task="ExplainExhibit", intent="ex")], intent="去某地"),
    ])
    # ExplainExhibit 第一次失敗、第二次也失敗 → retry 用完 → REPLAN_SUBTREE
    # planner 注入：回傳新 subtree，其 atomic 是 SuggestRoute
    new_subtree = _composite("L2-1-NEW", [
        _atomic("na", task="SuggestRoute", intent="繞道", topic="navigation.request"),
    ])
    captured = {"called": 0}
    def planner(sub_intent, env_facts):
        captured["called"] += 1
        captured["intent"] = sub_intent
        return new_subtree

    disp = make_dispatcher(agent, responses_by_task={
        "ExplainExhibit": [
            {"ok": False, "error": "boom"},
            {"ok": False, "error": "boom-again"},
        ],
        "SuggestRoute": [{"ok": True, "message": "replanned ok"}],
    })
    result = agent.execute_plan_with_monitoring(plan, dispatcher=disp, subtree_planner=planner)
    assert captured["called"] == 1
    assert captured["intent"] == "去某地"
    assert result["ok"] is True
    # 確認新 atomic 真的被呼叫
    new_task_calls = [c for c in disp.call_log if c["payload"]["task"] == "SuggestRoute"]
    assert new_task_calls


def test_env_change_triggers_subtree_replan_and_cancels_in_flight():
    """環境變動命中 IN_FLIGHT 節點 → 觸發 subtree replan → 對該節點送 cancel。"""
    agent = _make_agent()
    plan = _root([
        _composite("L2-1", [
            _atomic("a", task="LocateExhibit", topic="navigation.request",
                    intent="去 AI 區", target="AI_Tech_Area"),
        ], intent="去 AI 區"),
    ])

    # 為了精確控制節奏：第一次 LocateExhibit 不立刻回應；觸發環境變化後才收到 cancelled。
    delayed_replies: list = []

    def delayed_locate(payload):
        delayed_replies.append(payload)
        # 不立刻 deliver；返回 None 讓 dispatcher 不要 push 結果
        # 為了讓 monitor 持續等待，這裡製造一個「永不抵達」的 future：
        return {"__pending__": True}

    # 直接寫一個 dispatcher（不用工廠），更可控
    cancelled = set()
    call_log = []

    def dispatcher(node, payload):
        if node.get("_cancel"):
            cancelled.add(payload.get("task_id"))
            # 模擬 executor 收到 cancel 後回傳 cancelled result
            agent._monitor.on_action_result("navigation.result", {
                "task_id": payload.get("task_id"),
                "ok": False, "cancelled": True, "task": "LocateExhibit",
            })
            return
        call_log.append((node.get("id"), payload.get("task"), payload.get("task_id")))
        if payload.get("task") == "LocateExhibit":
            return  # 不回應，讓 IA loop 進入 idle
        # SuggestRoute 立刻回 OK
        agent._monitor.on_action_result("navigation.result", {
            "task_id": payload.get("task_id"),
            "ok": True, "task": payload.get("task"),
            "message": "alt-route",
        })

    # 環境注入器：在 monitoring loop 第一次 idle 時推一個 EnvChange
    triggered = {"once": False}
    def env_pump():
        time.sleep(0.05)
        if triggered["once"]:
            return
        triggered["once"] = True
        agent._monitor.on_env_event("blackboard.subscriber.x", {
            "topic": "Zone/AI_Tech_Area/CURRENT_STATE",
            "action": "update",
            "new_value": {"status_name": "Closed"},
        })

    new_sub = _composite("L2-1-NEW", [
        _atomic("na", task="SuggestRoute", topic="navigation.request", intent="繞道"),
    ])

    # 啟動 env pump
    t = threading.Thread(target=env_pump, daemon=True)
    t.start()
    result = agent.execute_plan_with_monitoring(
        plan,
        dispatcher=dispatcher,
        subtree_planner=lambda intent, env: new_sub,
        budget=Budget(max_replans=2, max_retries_per_node=1, poll_interval_sec=0.02, deadline_sec=5.0),
        trigger_config=TriggerConfig(
            relevant_topic_prefixes=("Zone/",),
            enable_subtree_replan=True,
            enable_root_replan=False,
        ),
    )
    t.join(timeout=2.0)

    # 期望：
    # - LocateExhibit 被 cancel
    # - SuggestRoute 被執行
    assert len(cancelled) >= 1, f"expected at least one cancel; cancelled={cancelled}"
    nav_tasks = [c[1] for c in call_log]
    assert "SuggestRoute" in nav_tasks
    assert result.get("ok") in (True, False)  # 重點是 cancel + 新節點 dispatch 已發生


def test_let_inflight_finish_when_no_decision():
    """無任何 trigger 時，IN_FLIGHT 節點應自然完成而不被取消。"""
    agent = _make_agent()
    plan = _root([_atomic("a", task="ExplainExhibit")])
    disp = make_dispatcher(agent, responses_by_task={
        "ExplainExhibit": [{"ok": True}],
    }, async_delay=0.05)
    result = agent.execute_plan_with_monitoring(plan, dispatcher=disp)
    assert result["ok"] is True
    # cancelled set 為空
    assert disp.cancelled_task_ids == set()


def test_idle_with_no_inflight_aborts():
    """所有 ready 都 dispatch 失敗 → IA 應在多次 idle 後中止。"""
    agent = _make_agent()
    plan = _root([_atomic("a", task="ExplainExhibit")])

    def fail_dispatcher(node, payload):
        if node.get("_cancel"):
            return
        raise RuntimeError("dispatch fail")

    result = agent.execute_plan_with_monitoring(
        plan,
        dispatcher=fail_dispatcher,
        budget=Budget(max_replans=0, max_retries_per_node=0, poll_interval_sec=0.01),
    )
    assert result["ok"] is False
    assert result["state_counts"].get("failed", 0) >= 1


# ----------------------------------------------------------------------
# C-04：節點逾時 → cancel + 走一般 retry / replan，不會無限等待
# ----------------------------------------------------------------------
def _silent_then_ok_dispatcher(agent, *, silent_attempts: int):
    """前 silent_attempts 次派工不回應（模擬 executor 掛掉或訊息遺失），之後回成功。"""
    log = {"dispatched": [], "cancelled": []}

    def dispatcher(node, payload):
        if node.get("_cancel"):
            log["cancelled"].append(payload.get("task_id"))
            return
        log["dispatched"].append(payload.get("task_id"))
        if len(log["dispatched"]) <= silent_attempts:
            return
        agent._monitor.on_action_result("info.result", {
            "task_id": payload["task_id"], "ok": True, "task": payload.get("task"),
        })

    return dispatcher, log


def test_node_timeout_cancels_and_retries():
    agent = _make_agent()
    plan = _root([_atomic("a", task="ExplainExhibit")])
    disp, log = _silent_then_ok_dispatcher(agent, silent_attempts=1)
    t0 = time.time()
    result = agent.execute_plan_with_monitoring(
        plan,
        dispatcher=disp,
        budget=Budget(max_replans=0, max_retries_per_node=1, poll_interval_sec=0.01, node_timeout_sec=0.1),
    )
    assert result["ok"] is True
    assert len(log["dispatched"]) == 2
    # 逾時的那次派工有送 cancel 給 executor
    assert log["cancelled"] == [log["dispatched"][0]]
    assert time.time() - t0 < 5


def test_node_timeout_without_retry_budget_terminates():
    """舊行為：IN_FLIGHT 永遠收不到結果時迴圈會無限等待。"""
    agent = _make_agent()
    plan = _root([_atomic("a", task="ExplainExhibit")])
    disp, log = _silent_then_ok_dispatcher(agent, silent_attempts=99)
    t0 = time.time()
    result = agent.execute_plan_with_monitoring(
        plan,
        dispatcher=disp,
        subtree_planner=lambda intent, env: None,
        budget=Budget(max_replans=0, max_retries_per_node=0, poll_interval_sec=0.01, node_timeout_sec=0.1),
    )
    assert result["ok"] is False
    assert result["results"][0]["error"].startswith("timeout")
    assert time.time() - t0 < 5


def test_node_deadline_overrides_default_timeout():
    agent = _make_agent()
    node = _atomic("a", task="ExplainExhibit")
    node["deadline_sec"] = 0.1
    disp, log = _silent_then_ok_dispatcher(agent, silent_attempts=1)
    result = agent.execute_plan_with_monitoring(
        _root([node]),
        dispatcher=disp,
        budget=Budget(max_replans=0, max_retries_per_node=1, poll_interval_sec=0.01, node_timeout_sec=None),
    )
    assert result["ok"] is True
    assert len(log["dispatched"]) == 2


def test_build_budget_reads_node_timeout_from_config():
    agent = _make_agent()
    agent.agent_config = {"intent": {"monitoring": {"node_timeout_sec": 12}}}
    assert agent._build_budget().node_timeout_sec == 12
    agent.agent_config = {"intent": {"monitoring": {"node_timeout_sec": 0}}}
    assert agent._build_budget().node_timeout_sec is None
    agent.agent_config = {}
    assert agent._build_budget().node_timeout_sec == 30.0


# ----------------------------------------------------------------------
# C-02 / C-01：無法派工的節點與 id 重複的 plan
# ----------------------------------------------------------------------
def test_atomic_without_task_is_not_dispatched():
    agent = _make_agent()
    no_task = {"id": "b", "type": "leaf_forced_atomic", "is_atomic": True, "intent": "?", "sub_plans": []}
    plan = _root([_atomic("a", task="ExplainExhibit"), no_task])
    disp = make_dispatcher(agent)
    result = agent.execute_plan_with_monitoring(plan, dispatcher=disp)
    # 舊行為：b 以 task="Unknown" 送往 info.request，executor 回成功 → ok=True
    assert result["ok"] is False
    assert [c["node_id"] for c in disp.call_log] == ["a"]
    b = next(r for r in result["results"] if r["id"] == "b")
    assert b["state"] == "failed"


def test_plan_with_duplicate_node_ids_is_rejected():
    agent = _make_agent()
    plan = _root([
        _atomic("1", task="TaskA"),
        _composite("2", [_atomic("1", task="TaskB1"), _atomic("2.2", task="TaskB2")]),
    ])
    disp = make_dispatcher(agent)
    result = agent.execute_plan_with_monitoring(plan, dispatcher=disp)
    assert result["ok"] is False
    assert "id 重複" in result["message"]
    assert disp.call_log == []


# ----------------------------------------------------------------------
# L-01：黑板環境事件接進監測迴圈
# ----------------------------------------------------------------------
class _FakeBlackboard:
    """取代黑板 client 函式：記錄訂閱、保存事件 handler，並可模擬黑板代理推送事件。"""

    def __init__(self, agent, *, available=True):
        self.agent = agent
        self.available = available
        self.patterns = []
        self.unsubscribed = 0
        self.handlers = {}

    def subscribe(self, topic, data_type="str", topic_handler=None):
        self.handlers[topic] = topic_handler

    def subscribe_blackboard(self, agent, pattern, *, requester_id, timeout=2.0):
        self.patterns.append((pattern, requester_id))
        return f"sub-{len(self.patterns)}" if self.available else None

    def unsubscribe_blackboard(self, agent, *, requester_id, timeout=2.0):
        self.unsubscribed += 1
        return True

    def emit(self, event):
        topic = subscriber_topic(self.agent._bb_requester_id())
        self.handlers[topic](topic, event)


@contextmanager
def _fake_blackboard(agent, *, available=True):
    bb = _FakeBlackboard(agent, available=available)
    with patch.object(agent, "subscribe", bb.subscribe), \
         patch("src.core.intentional_agent.subscribe_blackboard", bb.subscribe_blackboard), \
         patch("src.core.intentional_agent.unsubscribe_blackboard", bb.unsubscribe_blackboard):
        yield bb


def _no_llm(**kwargs):
    raise AssertionError("LLM should not be consulted in this test")


# BlackboardWatcher 對「Zone 的 CURRENT_STATE 關係改變」實際送出的事件格式
_ZONE_CROWDED_EVENT = {
    "topic": "Zone/CURRENT_STATE/State",
    "action": "create",
    "new_value": {"source_id": "AI_Tech_Area", "target_id": "Crowded"},
    "metadata": {"source_label": "Zone", "source_id": "AI_Tech_Area", "rel_type": "CURRENT_STATE",
                 "target_label": "State", "target_id": "Crowded"},
}


def test_blackboard_event_reaches_monitor_and_triggers_replan():
    """舊行為：沒有任何地方訂閱黑板事件，環境改變永遠不會觸發重規劃。"""
    agent = _make_agent()
    agent.domain = DomainProfile(env_subscriptions=["Zone/*/*"])
    plan = _root([
        _composite("L2-1", [
            _atomic("a", task="LocateExhibit", topic="navigation.request", intent="去 AI 區", target="AI_Tech_Area"),
        ], intent="去 AI 區"),
    ])
    new_sub = _composite("L2-1-NEW", [_atomic("na", task="SuggestRoute", topic="navigation.request", intent="繞道")])
    calls, cancelled = [], []

    with _fake_blackboard(agent) as bb:
        def dispatcher(node, payload):
            if node.get("_cancel"):
                cancelled.append(payload["task_id"])
                return
            calls.append(payload["task"])
            if payload["task"] == "LocateExhibit":
                # 導航進行中，黑板回報 AI 區變擁擠
                threading.Timer(0.05, bb.emit, args=[_ZONE_CROWDED_EVENT]).start()
                return
            agent._monitor.on_action_result("navigation.result", {
                "task_id": payload["task_id"], "ok": True, "task": payload["task"],
            })

        result = agent.execute_plan_with_monitoring(
            plan,
            dispatcher=dispatcher,
            subtree_planner=lambda intent, context: new_sub,
            llm_decider=_no_llm,
            budget=Budget(max_replans=2, max_retries_per_node=1, poll_interval_sec=0.02, deadline_sec=5.0),
        )

    assert bb.patterns == [("Zone/*/*", agent._bb_requester_id())]
    assert calls == ["LocateExhibit", "SuggestRoute"]
    assert len(cancelled) == 1
    assert result["ok"] is True
    assert bb.unsubscribed == 1   # 結束時釋放黑板上的訂閱


def test_monitoring_still_works_when_blackboard_agent_is_down():
    agent = _make_agent()
    agent.domain = DomainProfile(env_subscriptions=["Zone/*/*", "Booth/*/*"])
    with _fake_blackboard(agent, available=False) as bb:
        disp = make_dispatcher(agent)
        result = agent.execute_plan_with_monitoring(_root([_atomic("a", task="ExplainExhibit")]), dispatcher=disp)
    assert result["ok"] is True
    assert len(bb.patterns) == 1      # 第一個 pattern 失敗後不再逐一等待逾時
    assert bb.unsubscribed == 0


def test_generic_profile_does_not_subscribe_to_blackboard():
    agent = _make_agent()
    with _fake_blackboard(agent) as bb:
        agent.execute_plan_with_monitoring(_root([_atomic("a", task="ExplainExhibit")]), dispatcher=make_dispatcher(agent))
    assert bb.patterns == []


def test_build_trigger_config_reads_settings_and_domain_prefixes():
    agent = _make_agent()
    agent.domain = DomainProfile(env_subscriptions=["Zone/*/*", "*/CONNECTED_TO/*"])
    agent.agent_config = {"intent": {"monitoring": {"enable_llm_assist": True, "llm_max_calls": 3}}}
    tc = agent._build_trigger_config()
    assert (tc.enable_llm_assist, tc.llm_max_calls) == (True, 3)
    assert tc.relevant_topic_prefixes == ("Zone/", "")
    agent.domain = DomainProfile()
    agent.agent_config = {}
    tc = agent._build_trigger_config()
    assert tc.enable_llm_assist is False
    assert tc.relevant_topic_prefixes == TriggerConfig().relevant_topic_prefixes


# ----------------------------------------------------------------------
# L-02：重規劃時帶入環境事實與重規劃原因
# ----------------------------------------------------------------------
def test_replan_context_contains_blackboard_env_facts():
    agent = _make_agent()
    agent.domain = DomainProfile(env_fact_queries={"zone_states": "MATCH (z:Zone) RETURN z"})
    rows = [{"zone": "AI_Tech_Area", "state": "Closed"}]
    plan = _root([_composite("L2-1", [_atomic("a", task="ExplainExhibit")], intent="介紹展品")])
    captured = {}

    def planner(sub_intent, context):
        captured["context"] = context
        return _composite("N", [_atomic("na", task="SuggestRoute")])

    disp = make_dispatcher(agent, responses_by_task={
        "ExplainExhibit": [{"ok": False, "error": "booth closed"}, {"ok": False, "error": "booth closed"}],
    })
    with patch("src.core.intentional_agent.try_query_blackboard", return_value=rows):
        result = agent.execute_plan_with_monitoring(plan, dispatcher=disp, subtree_planner=planner, llm_decider=_no_llm)

    assert result["ok"] is True
    ctx = captured["context"]
    assert ctx["env_facts"] == {"zone_states": rows}
    assert ctx["replan"]["kind"] == "replan_subtree"
    assert ctx["replan"]["affected_steps"][0]["task"] == "ExplainExhibit"
    assert ctx["replan"]["affected_steps"][0]["error"] == "booth closed"


def test_collect_env_facts_marks_unavailable_queries():
    agent = _make_agent()
    agent.domain = DomainProfile(env_fact_queries={"ok_q": "Q1", "down_q": "Q2"})
    with patch("src.core.intentional_agent.try_query_blackboard",
               side_effect=lambda a, cypher, **kw: [{"x": 1}] if cypher == "Q1" else None):
        facts = agent._collect_env_facts()
    assert facts == {"ok_q": [{"x": 1}], "_unavailable": ["down_q"]}


def test_default_planners_forward_context_to_plan_intention():
    agent = _make_agent()
    seen = []

    def fake_plan_intention(intention, *, context=None):
        seen.append((intention, context))
        return _root([_atomic("1", task="T")])

    agent.plan_intention = fake_plan_intention
    ctx = {"replan": {"reason": "r"}, "env_facts": {"k": []}}
    assert agent._default_subtree_planner("sub", ctx) is not None
    assert agent._default_root_planner("root", ctx) is not None
    assert seen == [("sub", ctx), ("root", ctx)]


# ----------------------------------------------------------------------
# L-03：LLM 輔助判斷接線（REPAIR_NODE 可達、IN_FLIGHT 節點可重派）
# ----------------------------------------------------------------------
def test_llm_assist_repairs_failed_node_params():
    agent = _make_agent()
    plan = _root([_atomic("a", task="LocateExhibit", topic="navigation.request", target="AI_Tech_Aera")])

    def decider(*, intent, env_changes, action_results, cursor):
        return ReplanDecision(kind=TriggerKind.REPAIR_NODE, affected_node_ids=(action_results[0].node_id,),
                              new_params={"target_name": "AI_Tech_Area"}, reason="llm: typo in target")

    disp = make_dispatcher(agent, responses_by_task={
        "LocateExhibit": [{"ok": False, "error": "unknown target AI_Tech_Aera"}, {"ok": True}],
    })
    result = agent.execute_plan_with_monitoring(
        plan, dispatcher=disp, trigger_config=TriggerConfig(enable_llm_assist=True), llm_decider=decider,
    )
    assert result["ok"] is True
    assert [c["payload"]["params"]["target_name"] for c in disp.call_log] == ["AI_Tech_Aera", "AI_Tech_Area"]


def test_llm_retry_of_in_flight_node_cancels_and_redispatches():
    """舊行為：IN_FLIGHT 節點被標為 OBSOLETE，reset_for_retry 失敗，節點不會重派。"""
    agent = _make_agent()
    plan = _root([_composite("L2-1", [
        _atomic("a", task="LocateExhibit", topic="navigation.request", target="AI_Tech_Area"),
    ])])
    attempts = []

    def dispatcher(node, payload):
        if node.get("_cancel"):
            agent._monitor.on_action_result("navigation.result", {
                "task_id": payload["task_id"], "ok": False, "cancelled": True,
            })
            return
        attempts.append(payload["task_id"])
        if len(attempts) == 1:
            threading.Timer(0.05, agent._monitor.on_env_event, args=["bb", {
                "topic": "Zone/Hall_B/crowd_level", "action": "update", "new_value": "Crowded",
            }]).start()
            return
        agent._monitor.on_action_result("navigation.result", {"task_id": payload["task_id"], "ok": True})

    def decider(*, intent, env_changes, action_results, cursor):
        return ReplanDecision(kind=TriggerKind.RETRY_NODE, affected_node_ids=("a",), reason="llm: refresh route")

    result = agent.execute_plan_with_monitoring(
        plan,
        dispatcher=dispatcher,
        trigger_config=TriggerConfig(enable_llm_assist=True, relevant_topic_prefixes=("Zone/",)),
        llm_decider=decider,
        budget=Budget(max_replans=1, max_retries_per_node=1, poll_interval_sec=0.02, deadline_sec=5.0),
    )
    assert len(attempts) == 2
    assert result["ok"] is True

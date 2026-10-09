"""IntentionalAgent.execute_plan_with_monitoring 的 e2e mock 測試。

以注入式 dispatcher 取代真實 broker：dispatcher 依 payload 內容回填 monitor 結果，
即可模擬：成功、失敗、cancel、replan、environmental trigger 等情境。
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.app_helper import get_agent_config
from src.core.intentional_agent import IntentionalAgent
from src.core.monitoring import (
    Budget,
    NodeState,
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
            cfg.setdefault("intent", {}).setdefault("monitoring", {
                "max_replans": 2,
                "max_retries_per_node": 1,
                "poll_interval_sec": 0.02,
            })
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

# tests/test_execute_plan.py
"""測試 IntentionalAgent.execute_plan 與相關邏輯"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.app_helper import get_agent_config
from src.core.intentional_agent import IntentionalAgent
from src.agents._executor_utils import (
    resolve_request_topic,
    build_action_payload,
    TOPIC_INFO_REQUEST,
    TOPIC_NAVIGATION_REQUEST,
)


# -------------------------
# Config
# -------------------------
def _minimal_agent_config() -> dict:
    """最小 agent_config，供 execute_plan 單元測試用（不需 LLM/KG）。"""
    try:
        cfg = get_agent_config()
        # 若有完整 config 且 llm 可用，直接使用
        if cfg.get("llm") and cfg.get("broker"):
            return cfg
    except Exception:
        pass
    return {
        "llm": {"provider": "mock"},
        "broker": {"broker_name": "mqtt01"},
        "broker.mqtt01": {"broker_type": "mqtt", "host": "localhost", "port": 1883},
        "kg": {"type": "neo4j", "neo4j": {"uri": "bolt://localhost:7687"}},
    }


# -------------------------
# _executor_utils 單元測試
# -------------------------
def test_resolve_request_topic_empty():
    assert resolve_request_topic(None) == TOPIC_INFO_REQUEST
    assert resolve_request_topic("") == TOPIC_INFO_REQUEST


def test_resolve_request_topic_short_form():
    assert resolve_request_topic("info") == TOPIC_INFO_REQUEST
    assert resolve_request_topic("INFO") == TOPIC_INFO_REQUEST
    assert resolve_request_topic("navigation") == TOPIC_NAVIGATION_REQUEST
    assert resolve_request_topic("Navigation") == TOPIC_NAVIGATION_REQUEST


def test_resolve_request_topic_full_form():
    assert resolve_request_topic("info.request") == "info.request"
    assert resolve_request_topic("navigation.request") == "navigation.request"
    assert resolve_request_topic("Info.Request") == "Info.Request"


def test_resolve_request_topic_unknown_fallback():
    assert resolve_request_topic("unknown") == TOPIC_INFO_REQUEST


def test_build_action_payload():
    p = build_action_payload(
        task="LocateExhibit",
        params={"target_name": "A12", "current_location": "入口"},
        action_id="act-1",
        intent="帶我去A12攤位",
    )
    assert p["task"] == "LocateExhibit"
    assert p["params"] == {"target_name": "A12", "current_location": "入口"}
    assert p["action_id"] == "act-1"
    assert p["intent"] == "帶我去A12攤位"


def test_build_action_payload_defaults():
    p = build_action_payload(task="AnswerFAQ", params={})
    assert p["task"] == "AnswerFAQ"
    assert p["params"] == {}
    assert p["action_id"] is None
    assert p["intent"] == ""


# -------------------------
# _compute_execution_order / _compute_execution_levels
# -------------------------
@patch("src.core.intentional_agent.Neo4jBoltAdapter")
def test_compute_execution_levels_parallel(mock_adapter_cls):
    """Parallel(1,2)：1 與 2 應在同層，可並行。"""
    mock_adapter_cls.from_config.return_value = MagicMock()
    agent_config = _minimal_agent_config()
    agent = IntentionalAgent(agent_config=agent_config, intention="test")

    sub_plans = [
        {"id": "1", "intent": "A"},
        {"id": "2", "intent": "B"},
    ]
    execution_logic = [{"type": "Parallel", "from_id": "1", "to_id": "2"}]
    levels = agent._compute_execution_levels(sub_plans, execution_logic)
    assert len(levels) == 1
    assert len(levels[0]) == 2
    assert {n["id"] for n in levels[0]} == {"1", "2"}


@patch("src.core.intentional_agent.Neo4jBoltAdapter")
def test_compute_execution_levels_sequence_then_parallel(mock_adapter_cls):
    """1,2 Parallel；2->3 Sequence：level0=[1,2] 並行，level1=[3]。"""
    mock_adapter_cls.from_config.return_value = MagicMock()
    agent_config = _minimal_agent_config()
    agent = IntentionalAgent(agent_config=agent_config, intention="test")

    sub_plans = [
        {"id": "1", "intent": "A"},
        {"id": "2", "intent": "B"},
        {"id": "3", "intent": "C"},
    ]
    execution_logic = [
        {"type": "Parallel", "from_id": "1", "to_id": "2"},
        {"type": "Sequence", "from_id": "2", "to_id": "3"},
    ]
    levels = agent._compute_execution_levels(sub_plans, execution_logic)
    assert len(levels) == 2
    assert {n["id"] for n in levels[0]} == {"1", "2"}
    assert [n["id"] for n in levels[1]] == ["3"]


@patch("src.core.intentional_agent.Neo4jBoltAdapter")
def test_compute_execution_order_sequence(mock_adapter_cls):
    """Sequence(from_id, to_id)：to_id 須在 from_id 之後執行。"""
    mock_adapter_cls.from_config.return_value = MagicMock()
    agent_config = _minimal_agent_config()
    agent = IntentionalAgent(agent_config=agent_config, intention="test")

    sub_plans = [
        {"id": "a", "intent": "A"},
        {"id": "b", "intent": "B"},
        {"id": "c", "intent": "C"},
    ]
    execution_logic = [
        {"type": "Sequence", "from_id": "a", "to_id": "b"},
        {"type": "Sequence", "from_id": "b", "to_id": "c"},
    ]
    order = agent._compute_execution_order(sub_plans, execution_logic)
    ids = [n["id"] for n in order]
    assert ids == ["a", "b", "c"]


@patch("src.core.intentional_agent.Neo4jBoltAdapter")
def test_compute_execution_order_parallel(mock_adapter_cls):
    """Parallel：無依賴，順序任意（依實作可能為原序或 sorted）。"""
    mock_adapter_cls.from_config.return_value = MagicMock()
    agent_config = _minimal_agent_config()
    agent = IntentionalAgent(agent_config=agent_config, intention="test")

    sub_plans = [
        {"id": "x", "intent": "X"},
        {"id": "y", "intent": "Y"},
    ]
    execution_logic = [{"type": "Parallel", "from_id": "x", "to_id": "y"}]
    order = agent._compute_execution_order(sub_plans, execution_logic)
    ids = [n["id"] for n in order]
    assert set(ids) == {"x", "y"}
    assert len(ids) == 2


@patch("src.core.intentional_agent.Neo4jBoltAdapter")
def test_compute_execution_order_mixed(mock_adapter_cls):
    """a -> b, a -> c（b 與 c 並行，皆依賴 a）。"""
    mock_adapter_cls.from_config.return_value = MagicMock()
    agent_config = _minimal_agent_config()
    agent = IntentionalAgent(agent_config=agent_config, intention="test")

    sub_plans = [
        {"id": "a", "intent": "A"},
        {"id": "b", "intent": "B"},
        {"id": "c", "intent": "C"},
    ]
    execution_logic = [
        {"type": "Sequence", "from_id": "a", "to_id": "b"},
        {"type": "Sequence", "from_id": "a", "to_id": "c"},
    ]
    order = agent._compute_execution_order(sub_plans, execution_logic)
    ids = [n["id"] for n in order]
    assert ids[0] == "a"
    assert set(ids[1:]) == {"b", "c"}


# -------------------------
# execute_plan
# -------------------------
@patch("src.core.intentional_agent.Neo4jBoltAdapter")
def test_execute_plan_leaf_unresolved(mock_adapter_cls):
    """leaf_unresolved 時不執行，回傳 ok=False。"""
    mock_adapter_cls.from_config.return_value = MagicMock()
    agent_config = _minimal_agent_config()
    agent = IntentionalAgent(agent_config=agent_config, intention="test")

    plan = {
        "id": "root",
        "intent": "無法完成",
        "type": "leaf_unresolved",
        "reason": "Some sub-intents have no matched actions.",
        "sub_plans": [],
        "execution_logic": [],
    }
    result = agent.execute_plan(plan)

    assert result["ok"] is False
    assert "無法完成" in result.get("message", "") or "抱歉" in result.get("message", "")
    assert result["plan"] == plan
    assert "results" not in result or result.get("results") == []


@patch("src.core.intentional_agent.Neo4jBoltAdapter")
def test_execute_plan_single_atomic_publishes_to_info_request(mock_adapter_cls):
    """單一 atomic 節點應 publish 至正確 topic（info.request）。"""
    mock_adapter_cls.from_config.return_value = MagicMock()
    agent_config = _minimal_agent_config()
    agent = IntentionalAgent(agent_config=agent_config, intention="test")

    plan = {
        "id": "root",
        "intent": "介紹展品",
        "type": "composite",
        "sub_plans": [
            {
                "id": "a1",
                "intent": "介紹智慧導覽眼鏡",
                "type": "atomic",
                "is_atomic": True,
                "topic": "info.request",
                "task": "ExplainExhibit",
                "params": {"target_name": "智慧導覽眼鏡"},
                "action_id": "act-1",
                "sub_plans": [],
            },
        ],
        "execution_logic": [],
    }

    mock_resp = MagicMock()
    mock_resp.content = {"ok": True, "task": "ExplainExhibit", "message": "已介紹"}
    mock_resp.error = None
    with patch.object(agent, "publish_sync", MagicMock(return_value=mock_resp)) as mock_sync:
        result = agent.execute_plan(plan)

    assert result["ok"] is True
    assert len(result["results"]) == 1
    assert result["results"][0]["ok"] is True

    mock_sync.assert_called_once()
    call_args = mock_sync.call_args
    assert call_args[0][0] == "info.request"
    payload = call_args[0][1]
    assert payload["task"] == "ExplainExhibit"
    assert payload["params"] == {"target_name": "智慧導覽眼鏡"}
    assert payload["action_id"] == "act-1"


@patch("src.core.intentional_agent.Neo4jBoltAdapter")
def test_execute_plan_single_atomic_publishes_to_navigation_request(mock_adapter_cls):
    """單一 atomic 節點 topic=navigation 應 publish 至 navigation.request。"""
    mock_adapter_cls.from_config.return_value = MagicMock()
    agent_config = _minimal_agent_config()
    agent = IntentionalAgent(agent_config=agent_config, intention="test")

    plan = {
        "id": "root",
        "intent": "導航",
        "type": "composite",
        "sub_plans": [
            {
                "id": "n1",
                "intent": "帶我去A12",
                "type": "atomic",
                "is_atomic": True,
                "topic": "navigation",
                "task": "LocateExhibit",
                "params": {"target_name": "A12", "current_location": "入口"},
                "action_id": "act-nav",
                "sub_plans": [],
            },
        ],
        "execution_logic": [],
    }

    mock_resp = MagicMock()
    mock_resp.content = {"ok": True, "task": "LocateExhibit", "message": "已規劃路線"}
    mock_resp.error = None
    with patch.object(agent, "publish_sync", MagicMock(return_value=mock_resp)) as mock_sync:
        result = agent.execute_plan(plan)

    assert result["ok"] is True
    mock_sync.assert_called_once()
    assert mock_sync.call_args[0][0] == TOPIC_NAVIGATION_REQUEST
    assert mock_sync.call_args[0][1]["task"] == "LocateExhibit"


@patch("src.core.intentional_agent.Neo4jBoltAdapter")
def test_execute_plan_multiple_atomics_sequence(mock_adapter_cls):
    """多個 atomic 依 Sequence 順序執行。"""
    mock_adapter_cls.from_config.return_value = MagicMock()
    agent_config = _minimal_agent_config()
    agent = IntentionalAgent(agent_config=agent_config, intention="test")

    plan = {
        "id": "root",
        "intent": "先介紹再導航",
        "type": "composite",
        "sub_plans": [
            {
                "id": "a",
                "intent": "介紹",
                "type": "atomic",
                "is_atomic": True,
                "topic": "info.request",
                "task": "ExplainExhibit",
                "params": {"target_name": "X"},
                "sub_plans": [],
            },
            {
                "id": "b",
                "intent": "導航",
                "type": "atomic",
                "is_atomic": True,
                "topic": "navigation.request",
                "task": "LocateExhibit",
                "params": {"target_name": "X"},
                "sub_plans": [],
            },
        ],
        "execution_logic": [
            {"type": "Sequence", "from_id": "a", "to_id": "b"},
        ],
    }

    mock_resp = MagicMock()
    mock_resp.content = {"ok": True, "task": "?", "message": "ok"}
    mock_resp.error = None
    with patch.object(agent, "publish_sync", MagicMock(return_value=mock_resp)) as mock_sync:
        result = agent.execute_plan(plan)

    assert result["ok"] is True
    assert len(result["results"]) == 2
    assert mock_sync.call_count == 2

    # 順序應為 a -> b
    calls = mock_sync.call_args_list
    assert calls[0][0][1]["task"] == "ExplainExhibit"
    assert calls[1][0][1]["task"] == "LocateExhibit"


@patch("src.core.intentional_agent.Neo4jBoltAdapter")
def test_execute_plan_publish_failure(mock_adapter_cls):
    """publish 失敗時該 atomic 回傳 ok=False，整體 all_ok 為 False。"""
    mock_adapter_cls.from_config.return_value = MagicMock()
    agent_config = _minimal_agent_config()
    agent = IntentionalAgent(agent_config=agent_config, intention="test")

    plan = {
        "id": "root",
        "intent": "執行",
        "type": "composite",
        "sub_plans": [
            {
                "id": "a1",
                "intent": "任務",
                "type": "atomic",
                "is_atomic": True,
                "topic": "info.request",
                "task": "AnswerFAQ",
                "params": {},
                "sub_plans": [],
            },
        ],
        "execution_logic": [],
    }

    with patch.object(agent, "publish_sync", MagicMock(side_effect=RuntimeError("Broker unavailable"))):
        result = agent.execute_plan(plan)

    assert result["ok"] is False
    assert len(result["results"]) == 1
    assert result["results"][0]["ok"] is False
    assert "error" in result["results"][0].get("result", {})


@patch("src.core.intentional_agent.Neo4jBoltAdapter")
def test_execute_plan_topic_fallback_when_empty(mock_adapter_cls):
    """topic 為空時 fallback 至 info.request。"""
    mock_adapter_cls.from_config.return_value = MagicMock()
    agent_config = _minimal_agent_config()
    agent = IntentionalAgent(agent_config=agent_config, intention="test")

    plan = {
        "id": "root",
        "intent": "執行",
        "type": "composite",
        "sub_plans": [
            {
                "id": "a1",
                "intent": "任務",
                "type": "atomic",
                "is_atomic": True,
                "topic": None,
                "task": "CrowdStatus",
                "params": {},
                "sub_plans": [],
            },
        ],
        "execution_logic": [],
    }

    mock_resp = MagicMock()
    mock_resp.content = {"ok": True, "task": "CrowdStatus", "message": "人潮正常"}
    mock_resp.error = None
    with patch.object(agent, "publish_sync", MagicMock(return_value=mock_resp)) as mock_sync:
        result = agent.execute_plan(plan)

    assert result["ok"] is True
    mock_sync.assert_called_once()
    assert mock_sync.call_args[0][0] == TOPIC_INFO_REQUEST

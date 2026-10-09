"""InfoAgent 單元測試（不啟動 broker，直接呼叫 _handle / _handle_cancel）。"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from src.agents.info_agent import InfoAgent
from src.agents._executor_utils import (
    TOPIC_INFO_CANCEL,
    TOPIC_INFO_RESULT,
)


class _FakeParcel:
    def __init__(self, content):
        self.content = content


def _info_agent() -> InfoAgent:
    agent_config = {
        "broker": {"broker_name": "mqtt01", "mqtt01": {"broker_type": "empty"}},
    }
    # 直接 new；不啟動 broker
    return InfoAgent(agent_config=agent_config)


# ----------------------------------------------------------------------
# Backward compat（無 task_id 路徑）
# ----------------------------------------------------------------------
def test_handle_returns_dict_without_task_id():
    agent = _info_agent()
    with patch("src.agents.info_agent.time.sleep", lambda *a, **k: None):
        with patch.object(agent, "publish") as mock_pub:
            result = agent._handle("info.request", _FakeParcel({
                "task": "AnswerFAQ",
                "params": {"question": "Q1"},
                "action_id": "a-1",
                "intent": "i1",
            }))
    assert result["ok"] is True
    assert result["task"] == "AnswerFAQ"
    # 無 task_id：不應發佈 result
    mock_pub.assert_not_called()


# ----------------------------------------------------------------------
# Async path（帶 task_id 應發佈到 info.result）
# ----------------------------------------------------------------------
def test_handle_publishes_result_when_task_id_given():
    agent = _info_agent()
    with patch("src.agents.info_agent.time.sleep", lambda *a, **k: None):
        with patch.object(agent, "publish") as mock_pub:
            result = agent._handle("info.request", _FakeParcel({
                "task": "AnswerFAQ",
                "params": {"question": "Q1"},
                "action_id": "a-1",
                "intent": "i1",
                "task_id": "T-1",
            }))
    assert result["ok"] is True
    assert result["task_id"] == "T-1"
    mock_pub.assert_called_once()
    topic, payload = mock_pub.call_args[0]
    assert topic == TOPIC_INFO_RESULT
    assert payload["task_id"] == "T-1"
    assert payload["ok"] is True


# ----------------------------------------------------------------------
# Cancel mechanism
# ----------------------------------------------------------------------
def test_cancel_sets_cancel_token_for_inflight_task():
    agent = _info_agent()
    started = threading.Event()
    canceled = {"value": False}

    # 對 sleep 動手：第一次 sleep 後設 started flag
    real_sleep = time.sleep
    call_count = {"n": 0}

    def slow_sleep(s, *a, **k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            started.set()
        real_sleep(0.01)

    with patch("src.agents.info_agent.time.sleep", slow_sleep):
        with patch.object(agent, "publish") as mock_pub:
            # 起 thread 跑 _handle
            def worker():
                agent._handle("info.request", _FakeParcel({
                    "task": "AnswerFAQ",
                    "params": {"question": "Q1"},
                    "action_id": None,
                    "intent": "",
                    "task_id": "T-cancel",
                }))

            t = threading.Thread(target=worker, daemon=True)
            t.start()
            started.wait(timeout=2.0)
            # 發 cancel
            agent._handle_cancel("info.cancel", _FakeParcel({
                "task_id": "T-cancel",
                "reason": "user_requested",
            }))
            t.join(timeout=3.0)
            assert not t.is_alive(), "_handle 應該因 cancel 提早結束"

    # publish 應該包含 result 且 cancelled=True
    pub_calls = [c for c in mock_pub.call_args_list if c[0][0] == TOPIC_INFO_RESULT]
    assert pub_calls, "應發出 info.result"
    payload = pub_calls[0][0][1]
    assert payload["task_id"] == "T-cancel"
    # 因 cancel：ok=False, cancelled=True
    assert payload["cancelled"] is True
    assert payload["ok"] is False


def test_cancel_unknown_task_returns_error():
    agent = _info_agent()
    result = agent._handle_cancel("info.cancel", _FakeParcel({
        "task_id": "doesnt-exist",
        "reason": "x",
    }))
    assert result["ok"] is False
    assert "not in flight" in result["error"]


def test_cancel_missing_task_id_returns_error():
    agent = _info_agent()
    result = agent._handle_cancel("info.cancel", _FakeParcel({}))
    assert result["ok"] is False
    assert "missing task_id" in result["error"]


# ----------------------------------------------------------------------
# Idempotency
# ----------------------------------------------------------------------
def test_idempotency_returns_cached_on_same_key():
    agent = _info_agent()
    with patch("src.agents.info_agent.time.sleep", lambda *a, **k: None):
        with patch.object(agent, "publish"):
            r1 = agent._handle("info.request", _FakeParcel({
                "task": "AnswerFAQ",
                "params": {"question": "Q1"},
                "action_id": None,
                "intent": "",
                "task_id": "T-i1",
                "idempotency_key": "K-1",
            }))
            r2 = agent._handle("info.request", _FakeParcel({
                "task": "AnswerFAQ",
                "params": {"question": "QUITE-DIFFERENT"},
                "action_id": None,
                "intent": "",
                "task_id": "T-i2",
                "idempotency_key": "K-1",
            }))
    # 第二次應該直接拿到 cached 第一次的結果
    assert r1["message"] == r2["message"]
    assert r2["ok"] is True


# ----------------------------------------------------------------------
# C-02：缺少 task 的請求必須回報失敗
# ----------------------------------------------------------------------
@pytest.mark.parametrize("task", [None, "", "Unknown"])
def test_handle_missing_task_returns_failure(task):
    agent = _info_agent()
    payload = {"params": {}, "task_id": "T-missing"}
    if task is not None:
        payload["task"] = task
    with patch.object(agent, "publish") as mock_pub:
        result = agent._handle("info.request", _FakeParcel(payload))
    assert result["ok"] is False
    assert result["error"] == "missing task"
    topic, published = mock_pub.call_args[0]
    assert topic == TOPIC_INFO_RESULT
    assert published["ok"] is False

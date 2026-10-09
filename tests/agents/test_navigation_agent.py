"""NavigationAgent 單元測試（time.sleep 全 patch 為 no-op 以加速，
並 mock 掉黑板查詢避免真實 publish_sync 超時）。"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from src.agents.navigation_agent import NavigationAgent
from src.agents._executor_utils import (
    TOPIC_NAVIGATION_REQUEST,
    TOPIC_NAVIGATION_RESULT,
)


class _FakeParcel:
    def __init__(self, content):
        self.content = content


def _nav_agent() -> NavigationAgent:
    agent_config = {
        "broker": {"broker_name": "mqtt01", "mqtt01": {"broker_type": "empty"}},
    }
    return NavigationAgent(agent_config=agent_config)


def _publish_calls_to(mock_pub, topic):
    return [c for c in mock_pub.call_args_list if c[0][0] == topic]


@pytest.fixture
def fast_nav_env():
    """加速 navigation_agent：sleep 變 no-op、黑板查詢 mock 空回應。"""
    with patch("src.agents.navigation_agent.time.sleep", lambda *a, **k: None), \
         patch("src.agents.navigation_agent.get_crowd_hotspots", return_value=[]):
        yield


def test_handle_locate_exhibit_without_task_id(fast_nav_env):
    agent = _nav_agent()
    with patch.object(agent, "publish") as mock_pub:
        result = agent._handle("navigation.request", _FakeParcel({
            "task": "LocateExhibit",
            "params": {"target_name": "A12", "current_location": "入口"},
            "action_id": "n-1",
            "intent": "去 A12",
        }))
    assert result["ok"] is True
    # 不應發出 navigation.result（無 task_id）
    assert _publish_calls_to(mock_pub, TOPIC_NAVIGATION_RESULT) == []


def test_handle_locate_exhibit_with_task_id_publishes_result(fast_nav_env):
    agent = _nav_agent()
    with patch.object(agent, "publish") as mock_pub:
        result = agent._handle("navigation.request", _FakeParcel({
            "task": "LocateExhibit",
            "params": {"target_name": "A12", "current_location": "入口"},
            "action_id": None,
            "intent": "",
            "task_id": "NT-1",
        }))
    assert result["task_id"] == "NT-1"
    pub_calls = _publish_calls_to(mock_pub, TOPIC_NAVIGATION_RESULT)
    assert pub_calls
    assert pub_calls[0][0][1]["task_id"] == "NT-1"


def test_cancel_interrupts_locate_exhibit():
    agent = _nav_agent()
    started = threading.Event()
    real_sleep = time.sleep
    call_count = {"n": 0}

    def slow_sleep(s, *a, **k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            started.set()
        real_sleep(0.01)

    with patch("src.agents.navigation_agent.time.sleep", slow_sleep), \
         patch("src.agents.navigation_agent.get_crowd_hotspots", return_value=[]):
        with patch.object(agent, "publish") as mock_pub:
            def worker():
                agent._handle("navigation.request", _FakeParcel({
                    "task": "LocateExhibit",
                    "params": {"target_name": "A12", "current_location": "入口"},
                    "action_id": None,
                    "intent": "",
                    "task_id": "NT-cancel",
                }))

            t = threading.Thread(target=worker, daemon=True)
            t.start()
            started.wait(timeout=2.0)
            agent._handle_cancel("navigation.cancel", _FakeParcel({
                "task_id": "NT-cancel",
                "reason": "env_change:Zone/X/Closed",
            }))
            t.join(timeout=5.0)
            assert not t.is_alive(), "_handle 應因 cancel 提早結束"

    pubs = _publish_calls_to(mock_pub, TOPIC_NAVIGATION_RESULT)
    assert pubs
    payload = pubs[0][0][1]
    assert payload["task_id"] == "NT-cancel"
    assert payload["cancelled"] is True
    assert payload["ok"] is False

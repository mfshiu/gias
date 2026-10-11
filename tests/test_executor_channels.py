"""執行端頻道（G-01）：任何 <channel>.request 都能派工，不必修改核心程式。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents._executor_utils import (
    cancel_topic_for,
    channel_of_request_topic,
    channel_topics,
    progress_topic_for,
    result_topic_for,
)
from src.core.intentional_agent import IntentionalAgent


@pytest.mark.parametrize("topic, channel", [
    ("navigation.request", "navigation"),
    ("navigation", "navigation"),
    ("Robot", "robot"),
    ("robot.arm.request", "robot.arm"),
    ("robot.arm.progress", "robot.arm"),
    ("", None),
    (None, None),
])
def test_channel_of_topic(topic, channel):
    assert channel_of_request_topic(topic) == channel


def test_derived_topics_follow_the_convention():
    assert channel_topics("robot") == {"request": "robot.request", "result": "robot.result",
                                       "cancel": "robot.cancel", "progress": "robot.progress"}
    assert (result_topic_for("robot.request"), cancel_topic_for("robot"), progress_topic_for("robot.request")) == (
        "robot.result", "robot.cancel", "robot.progress")
    assert result_topic_for(None) is None


# ----------------------------------------------------------------------
# IntentionalAgent：依計畫中的頻道訂閱結果、派工與取消
# ----------------------------------------------------------------------
def _agent():
    cfg = {"llm": {"provider": "mock"}, "broker": {"broker_name": "mqtt01"},
           "kg": {"type": "neo4j", "neo4j": {"uri": "bolt://localhost:7687"}, "neo4j_actions": {}},
           "intent": {"monitoring": {"poll_interval_sec": 0.01, "max_replans": 1, "max_retries_per_node": 0,
                                     "enable_llm_assist": False}}}
    with patch("src.core.intentional_agent.Neo4jBoltAdapter") as adapter_cls, \
         patch("src.core.intentional_agent.LLMClient.from_config", return_value=MagicMock()):
        adapter_cls.from_config.return_value = MagicMock()
        return IntentionalAgent(agent_config=cfg, intention="test")


class _Broker:
    """記錄訂閱與發佈；收到 <channel>.request 時像執行端一樣回 <channel>.result。"""

    def __init__(self, agent, *, reply=True, failing=()):
        self.handlers: dict[str, object] = {}
        self.subscribe_calls: list[str] = []
        self.published: list[tuple[str, dict]] = []
        self.reply = reply
        self.failing = set(failing)          # 這些頻道的執行端回報失敗
        agent.subscribe = self.subscribe
        agent.publish = self.publish

    def subscribe(self, topic, data_type="str", handler=None):
        self.subscribe_calls.append(topic)
        self.handlers[topic] = handler

    def publish(self, topic, data=None):
        self.published.append((topic, data))
        if self.reply and topic.endswith(".request"):
            channel = topic[: -len(".request")]
            result_topic = channel + ".result"
            assert result_topic in self.handlers, f"published {topic} before subscribing {result_topic}"
            ok = channel not in self.failing
            self.handlers[result_topic](result_topic, {"task_id": data["task_id"], "ok": ok, "message": "done"})


def _atomic(nid, task, topic):
    return {"id": nid, "type": "atomic", "is_atomic": True, "intent": nid, "task": task, "topic": topic,
            "params": {}, "sub_plans": []}


def _plan(*nodes):
    return {"id": "root", "type": "composite", "intent": "x", "sub_plans": list(nodes), "execution_logic": []}


def test_new_executor_channel_is_dispatched_and_its_results_received():
    agent = _agent()
    broker = _Broker(agent)
    plan = _plan(_atomic("a", "PickItem", "robot.arm.request"), _atomic("b", "AnswerFAQ", "info.request"))
    result = agent.execute_plan_with_monitoring(plan)
    assert result["ok"] is True
    assert sorted(t for t, _ in broker.published) == ["info.request", "robot.arm.request"]
    assert {"robot.arm.result", "robot.arm.progress", "info.result", "info.progress"} <= set(broker.handlers)
    assert "navigation.result" not in broker.handlers           # 計畫沒用到的頻道不訂閱


def test_channel_of_a_replanned_subtree_is_subscribed_before_dispatch():
    """info 執行端失敗後重規劃，新子樹改用 drone 頻道：派工前先訂閱 drone.result。"""
    agent = _agent()
    broker = _Broker(agent, failing={"info"})
    sub = {"id": "L", "type": "composite", "intent": "巡查", "execution_logic": [],
           "sub_plans": [_atomic("a", "AnswerFAQ", "info.request")]}
    new_subtree = {"id": "N", "type": "composite", "intent": "巡查", "execution_logic": [],
                   "sub_plans": [_atomic("d", "Scan", "drone")]}
    result = agent.execute_plan_with_monitoring(_plan(sub), subtree_planner=lambda intent, ctx: new_subtree)
    assert result["ok"] is True
    assert [t for t, _ in broker.published] == ["info.request", "drone.request"]
    assert broker.subscribe_calls.index("drone.result") > broker.subscribe_calls.index("info.result")


def test_each_channel_is_subscribed_once():
    agent = _agent()
    broker = _Broker(agent)
    agent._ensure_result_subscriptions(["drone", "drone", "info"])
    agent._ensure_result_subscriptions(["drone"])
    assert sorted(broker.subscribe_calls) == ["drone.progress", "drone.result", "info.progress", "info.result"]


def test_cancel_goes_to_the_node_channel():
    agent = _agent()
    broker = _Broker(agent, reply=False)
    rec = MagicMock(node={"topic": "robot.arm.request"}, node_id="a", task_id="t-1")
    agent._send_cancel(rec, reason="stop", dispatcher=None)
    assert broker.published == [("robot.arm.cancel", {"task_id": "t-1", "reason": "stop"})]


def test_node_without_topic_is_not_dispatched():
    agent = _agent()
    broker = _Broker(agent)
    node = _atomic("a", "PickItem", None)
    assert agent._find_unexecutable_nodes(_plan(node))[0]["reason"] == "no_topic"
    result = agent.execute_plan_with_monitoring(_plan(node))
    assert result["ok"] is False and broker.published == []
    assert result["results"][0]["error"] == "node has no topic; not dispatched"

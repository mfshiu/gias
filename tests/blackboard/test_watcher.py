"""BlackboardWatcher 單元測試（P-03）：一次查詢、寫入即偵測、只掃描有人訂閱的 Label。"""

from __future__ import annotations

import re
import threading
import time
from unittest.mock import MagicMock

from src.blackboard.agent import BlackboardAgent
from src.blackboard.subscription import SubscriptionManager
from src.blackboard.watcher import BlackboardWatcher, WatchTarget


class _FakeBlackboard:
    """記憶體中的黑板；依查詢中出現的 Label 與關係型別回傳 build_poll_query 格式的資料列。"""

    def __init__(self):
        self.nodes: dict[str, dict[str, dict]] = {"Zone": {}, "Booth": {}}
        self.rels: dict[tuple[str, str], list[tuple[str, str, str]]] = {("Zone", "CURRENT_STATE"): []}
        self.queries: list[str] = []
        self.lock = threading.Lock()

    def query(self, cypher, params=None):
        with self.lock:
            self.queries.append(cypher)
            rows = []
            for part in cypher.split("UNION ALL"):
                label = re.search(r"'(\w+)' AS label", part).group(1)
                rel = re.search(r"\[r:`(\w+)`\]", part)
                if rel is None:
                    for node_id, props in self.nodes[label].items():
                        rows.append({"kind": "node", "label": label, "node_id": node_id, "neo4j_id": 1,
                                     "props": dict(props)})
                else:
                    for source, target_label, target_id in self.rels[(label, rel.group(1))]:
                        rows.append({"kind": "rel", "label": label, "node_id": source, "rel_type": rel.group(1),
                                     "target_label": target_label, "target_id": target_id, "rel_id": 7})
            return rows


TARGETS = [
    WatchTarget(label="Zone", id_property="name", watch_properties=["crowd_level"], watch_relationships=["CURRENT_STATE"]),
    WatchTarget(label="Booth", id_property="id", watch_properties=["status"]),
]


def _watcher(bb, *, only_subscribed=False, **kw):
    subs = SubscriptionManager()
    w = BlackboardWatcher(bb, subs, only_subscribed=only_subscribed, **kw)
    for t in TARGETS:
        w.add_watch_target(t)
    return w, subs


def _record(subs, pattern="*/*/*", subscriber="tester"):
    events = []
    sub_id = subs.subscribe(pattern, lambda e: events.append((e.topic, e.action.value)), subscriber)
    return events, sub_id


def _seed(bb):
    bb.nodes["Zone"]["AI_Tech_Area"] = {"crowd_level": "low"}
    bb.nodes["Booth"]["B_AI1"] = {"status": "open"}
    bb.rels[("Zone", "CURRENT_STATE")] = [("AI_Tech_Area", "State", "Normal")]


def test_each_poll_is_one_query_and_reports_changes():
    bb = _FakeBlackboard()
    _seed(bb)
    w, subs = _watcher(bb)
    events, _ = _record(subs)
    w._init_cache()
    assert len(bb.queries) == 1 and events == []                    # 建立快取不發事件

    bb.nodes["Zone"]["AI_Tech_Area"]["crowd_level"] = "high"
    bb.rels[("Zone", "CURRENT_STATE")] = [("AI_Tech_Area", "State", "Closed")]
    del bb.nodes["Booth"]["B_AI1"]
    bb.nodes["Booth"]["B_RB1"] = {"status": "open"}
    w._poll_once()
    assert len(bb.queries) == 2
    assert sorted(events) == sorted([
        ("Zone/AI_Tech_Area/crowd_level", "update"),
        ("Zone/CURRENT_STATE/State", "create"),
        ("Zone/CURRENT_STATE/State", "delete"),
        ("Booth/B_AI1/*", "delete"),
        ("Booth/B_RB1/*", "create"),
    ])


def test_only_subscribed_labels_are_scanned():
    bb = _FakeBlackboard()
    _seed(bb)
    w, subs = _watcher(bb, only_subscribed=True)
    w._init_cache()
    w._poll_once()
    assert bb.queries == []                                        # 沒有訂閱：不查詢

    events, _ = _record(subs, "Zone/*/*")
    w.prime_watched()                                             # 訂閱時先建立快取
    assert "Booth" not in bb.queries[-1] and events == []
    bb.nodes["Zone"]["AI_Tech_Area"]["crowd_level"] = "high"       # 訂閱後的變動
    bb.nodes["Booth"]["B_AI1"]["status"] = "closed"
    w._poll_once()
    assert events == [("Zone/AI_Tech_Area/crowd_level", "update")]
    assert all("Booth" not in q for q in bb.queries)


def test_label_watched_again_is_primed_without_stale_events():
    bb = _FakeBlackboard()
    _seed(bb)
    w, subs = _watcher(bb, only_subscribed=True)
    events, sub_id = _record(subs, "Zone/*/*")
    w.prime_watched()
    subs.unsubscribe(sub_id)
    w._poll_once()                                                # 不再被訂閱：丟掉快取
    bb.nodes["Zone"]["AI_Tech_Area"]["crowd_level"] = "high"       # 沒人訂閱時的變動不補發
    events2, _ = _record(subs, "Zone/*/*")
    w._poll_once()                                                # 未先 prime 時，第一輪只建立快取
    assert events2 == []
    bb.nodes["Zone"]["AI_Tech_Area"]["crowd_level"] = "low"
    w._poll_once()
    assert events2 == [("Zone/AI_Tech_Area/crowd_level", "update")]


def _wait(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_notify_change_polls_without_waiting_for_the_interval():
    bb = _FakeBlackboard()
    _seed(bb)
    w, subs = _watcher(bb, poll_interval_sec=30.0, min_poll_gap_sec=0.0)
    events, _ = _record(subs)
    w.start()
    try:
        assert _wait(lambda: len(bb.queries) >= 2)                 # 初始化 + 第一輪
        bb.nodes["Booth"]["B_AI1"]["status"] = "closed"
        w.notify_change()
        assert _wait(lambda: events == [("Booth/B_AI1/status", "update")], timeout=1.0)
    finally:
        started = time.monotonic()
        w.stop()
        assert time.monotonic() - started < 1.0                   # 停止不必等完整週期


def test_burst_of_notifications_is_coalesced():
    bb = _FakeBlackboard()
    w, _ = _watcher(bb, poll_interval_sec=30.0, min_poll_gap_sec=0.3)
    w.start()
    try:
        assert _wait(lambda: len(bb.queries) >= 2)
        before = len(bb.queries)
        for _ in range(20):
            w.notify_change()
            time.sleep(0.01)
        time.sleep(0.5)
        assert 1 <= len(bb.queries) - before <= 3
    finally:
        w.stop()


# ----------------------------------------------------------------------
# BlackboardAgent
# ----------------------------------------------------------------------
_EMPTY_BROKER = {"broker": {"broker_name": "mqtt01", "mqtt01": {"broker_type": "empty"}}}


def _agent(**kw):
    agent = BlackboardAgent(dict(_EMPTY_BROKER, **kw.pop("config", {})), **kw)
    agent._kg = MagicMock()
    agent.subscribe = MagicMock()
    agent.publish = MagicMock()
    return agent


def test_agent_writes_wake_the_watcher_and_subscriptions_prime_it():
    agent = _agent()
    agent.watcher = MagicMock()
    assert agent._handle_write({"cypher": "MATCH (n) SET n.x = 1"}, "tester")["ok"]
    agent.watcher.notify_change.assert_called_once()
    agent._handle_control("blackboard.control", {"command": "subscribe", "pattern": "Zone/*/*", "requester_id": "ia"})
    agent.watcher.prime_watched.assert_called_once()
    agent._handle_observe({"observation": {"observation_id": "o1", "entities": []}}, "observer")
    assert agent.watcher.notify_change.call_count == 2


def test_broadcast_can_be_turned_off():
    on = _agent()
    on.on_connected()
    assert [s["pattern"] for s in on.sub_manager.get_subscriptions()] == ["*/*/*"]
    assert on.watcher.only_subscribed is True
    on.watcher.stop()

    off = _agent(config={"blackboard": {"broadcast_events": False}})
    off.on_connected()
    assert off.sub_manager.count() == 0
    off.watcher.stop()


def test_watches_label_follows_subscription_patterns():
    subs = SubscriptionManager()
    assert subs.watches_label("Zone") is False
    zone = subs.subscribe("Zone/*/*", lambda e: None, "ia")
    assert subs.watches_label("Zone") and not subs.watches_label("Booth")
    subs.unsubscribe(zone)
    subs.subscribe("*/CURRENT_STATE/*", lambda e: None, "ia")       # 第一段為萬用字元：所有 Label 都要掃描
    assert subs.watches_label("Booth") and subs.watches_label("Agent")

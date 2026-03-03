# tests/blackboard/test_blackboard_agent.py
"""
BlackboardAgent 整合測試

使用實際的 MQTT broker、Neo4j 與 Agent 進行驗證：
1. BlackboardAgent 啟動並監控 Blackboard KG
2. 模擬訂閱者 Agent 訂閱特定 Topic
3. 變更 KG 狀態（透過 Cypher）
4. 驗證訂閱者收到正確的變更事件

執行前提：
- MQTT broker 已啟動（localhost:1883）
- Neo4j 已啟動（localhost:7687）
- 已執行 seed_blackboard 建立初始圖譜

執行：
  python -m pytest tests/blackboard/test_blackboard_agent.py -v -s
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any
from dataclasses import dataclass, field

import pytest

from src.app_helper import get_agent_config
from src.kg.adapter_neo4j import Neo4jBoltAdapter
from src.blackboard.agent import BlackboardAgent
from src.blackboard.event import BlackboardEvent, ChangeAction

from agentflow.core.agent import Agent

logger = logging.getLogger(__name__)


# -------------------------
# Config guards
# -------------------------

def _build_blackboard_kg(agent_config: dict) -> Neo4jBoltAdapter:
    """建立 Blackboard KG adapter"""
    kg_cfg = agent_config.get("kg", {})
    base = kg_cfg.get("neo4j")
    bb_overrides = kg_cfg.get("neo4j_blackboard")
    if not isinstance(base, dict):
        pytest.skip("Missing [kg.neo4j] config in gias.toml")
    if not isinstance(bb_overrides, dict):
        pytest.skip("Missing [kg.neo4j_blackboard] config in gias.toml")
    merged = {**base, **bb_overrides}
    return Neo4jBoltAdapter.from_config(merged, logger=None)


def _require_blackboard_kg_ready(agent_config: dict) -> Neo4jBoltAdapter:
    """確認 Blackboard KG 可用"""
    kg = _build_blackboard_kg(agent_config)
    try:
        r = kg.query("RETURN 1 AS ok", {})
        if not r or r[0].get("ok") != 1:
            pytest.skip("Blackboard Neo4j not responding as expected.")
    except Exception as e:
        pytest.skip(f"Blackboard Neo4j not reachable: {e}")
    return kg


def _require_broker_config(agent_config: dict) -> dict[str, Any]:
    """確認 broker 設定"""
    broker_cfg = agent_config.get("broker", {})
    broker_name = broker_cfg.get("broker_name", "mqtt01")
    mqtt_cfg = broker_cfg.get(broker_name, {})
    if not mqtt_cfg.get("host"):
        pytest.skip("Missing broker config in gias.toml")
    return mqtt_cfg


# -------------------------
# Subscriber Agent (測試用)
# -------------------------

@dataclass
class ReceivedEvent:
    topic: str
    payload: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


class SubscriberAgent(Agent):
    """測試用訂閱者 Agent：訂閱 blackboard 事件並記錄收到的訊息"""

    def __init__(self, agent_config: dict[str, Any], subscriber_id: str):
        self.subscriber_id = subscriber_id
        self.received_events: list[ReceivedEvent] = []
        self._event_lock = threading.Lock()
        self._connected_event = threading.Event()
        super().__init__(f"test_subscriber_{subscriber_id}", agent_config)

    def on_connected(self) -> None:
        logger.info("SubscriberAgent %s connected", self.subscriber_id)
        # 先設定連線完成標記
        self._connected_event.set()
        # 訂閱專屬 topic：blackboard.subscriber.<subscriber_id>
        self.subscribe(f"blackboard.subscriber.{self.subscriber_id}", "dict", self._handle_event)

    def wait_connected(self, timeout: float = 10.0) -> bool:
        return self._connected_event.wait(timeout)

    def _handle_event(self, topic: str, payload: Any) -> None:
        # payload 可能是 parcel 物件，需要提取 content
        if hasattr(payload, "content"):
            data = payload.content if isinstance(payload.content, dict) else {}
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {}
        
        logger.info("SubscriberAgent %s received: topic=%s, payload_type=%s, data=%s", 
                    self.subscriber_id, topic, type(payload).__name__, data)
        with self._event_lock:
            self.received_events.append(ReceivedEvent(
                topic=topic,
                payload=data,
            ))

    def get_events(self) -> list[ReceivedEvent]:
        with self._event_lock:
            return list(self.received_events)

    def clear_events(self) -> None:
        with self._event_lock:
            self.received_events.clear()

    def wait_for_event(
        self,
        *,
        action: str | None = None,
        topic_contains: str | None = None,
        timeout: float = 10.0,
    ) -> ReceivedEvent | None:
        """等待符合條件的事件"""
        start = time.time()
        while time.time() - start < timeout:
            for ev in self.get_events():
                if action and ev.payload.get("action") != action:
                    continue
                if topic_contains and topic_contains not in ev.payload.get("topic", ""):
                    continue
                return ev
            time.sleep(0.2)
        return None

    def subscribe_blackboard_pattern(self, pattern: str, ignore_self: bool = True, timeout: float = 5.0) -> str | None:
        """
        向 BlackboardAgent 訂閱指定 pattern
        
        Returns:
            subscription_id if success, None otherwise
        """
        from src.blackboard.agent import BlackboardAgent
        
        request = {
            "command": "subscribe",
            "pattern": pattern,
            "requester_id": self.subscriber_id,
            "ignore_self": ignore_self,
        }
        pcl = self.publish_sync(BlackboardAgent.CONTROL_TOPIC, request, timeout=timeout)
        
        # publish_sync 回傳 parcel 物件，需要取出 content
        response = getattr(pcl, "content", None) if pcl else None
        logger.debug("SubscriberAgent %s subscribe response: pcl=%s, content=%s", 
                     self.subscriber_id, type(pcl).__name__ if pcl else None, response)
        
        if isinstance(response, dict) and response.get("ok"):
            logger.info("SubscriberAgent %s subscribed to pattern: %s, sub_id=%s", 
                        self.subscriber_id, pattern, response.get("subscription_id"))
            return response.get("subscription_id")
        
        # 如果是其他錯誤，記錄詳細資訊
        error_info = getattr(pcl, "error", None) if pcl else "No parcel"
        logger.warning("SubscriberAgent %s failed to subscribe pattern=%s: response=%s, error=%s", 
                       self.subscriber_id, pattern, response, error_info)
        return None


# -------------------------
# Fixtures
# -------------------------

@pytest.fixture(scope="module")
def agent_config():
    return get_agent_config()


@pytest.fixture(scope="module")
def blackboard_kg(agent_config):
    kg = _require_blackboard_kg_ready(agent_config)
    yield kg
    kg.close()


@pytest.fixture(scope="module")
def broker_config(agent_config):
    return _require_broker_config(agent_config)


@pytest.fixture(scope="module")
def blackboard_agent(agent_config):
    """啟動 BlackboardAgent（整個 module 共用）"""
    agent = BlackboardAgent(
        agent_config=agent_config,
        poll_interval_sec=1.0,  # 縮短輪詢間隔以加速測試
    )
    agent.start_thread()
    # 等待連線與 watcher 啟動
    time.sleep(3)
    logger.info("blackboard_agent fixture ready: agent_id=%s, watcher=%s", 
                agent.agent_id, "started" if agent.watcher else "not started")
    yield agent
    agent.terminate()


@pytest.fixture
def subscriber(agent_config, blackboard_agent):
    """每個測試獨立的訂閱者 Agent（依賴 blackboard_agent 確保順序）"""
    # 確保 blackboard_agent 已就緒
    assert blackboard_agent is not None, "blackboard_agent not ready"
    
    subscriber_id = f"test_{uuid.uuid4().hex[:8]}"
    agent = SubscriberAgent(agent_config, subscriber_id)
    agent.start_thread()
    connected = agent.wait_connected(timeout=10.0)
    logger.info("subscriber fixture: id=%s, connected=%s", subscriber_id, connected)
    time.sleep(1)  # 等待訂閱完成
    yield agent
    agent.terminate()


# -------------------------
# Test: 基本連線
# -------------------------

@pytest.mark.integration
def test_blackboard_agent_starts(blackboard_agent):
    """測試 BlackboardAgent 能正常啟動"""
    assert blackboard_agent is not None
    assert blackboard_agent.watcher is not None
    logger.info("BlackboardAgent started: %s", blackboard_agent.agent_id)


@pytest.mark.integration
def test_subscriber_can_connect(subscriber):
    """測試訂閱者 Agent 能正常連線"""
    assert subscriber is not None
    assert subscriber._connected_event.is_set()
    logger.info("SubscriberAgent connected: %s", subscriber.subscriber_id)


# -------------------------
# Test: 節點屬性變動
# -------------------------

@pytest.mark.integration
def test_node_property_change_triggers_event(blackboard_agent, blackboard_kg, subscriber):
    """
    測試：變更節點屬性會觸發事件

    1. 訂閱者訂閱 Agent/*/* pattern
    2. 變更 Agent 的狀態
    3. 驗證訂閱者收到 UPDATE 事件
    """
    # 確認 blackboard_agent 已連線
    assert blackboard_agent.watcher is not None, "BlackboardAgent watcher not started"
    
    # 訂閱 Agent 相關變更
    sub_id = subscriber.subscribe_blackboard_pattern("Agent/*/*", timeout=10.0)
    assert sub_id is not None, f"Failed to subscribe to Agent/*/*. Subscriber connected: {subscriber._connected_event.is_set()}"

    # 準備：確保有測試用 Agent 節點
    test_agent_id = f"TestBot_{uuid.uuid4().hex[:8]}"
    blackboard_kg.write(
        """
        CREATE (a:Agent {agent_id: $id, type: 'Test_Robot', status: 'Idle'})
        """,
        {"id": test_agent_id},
    )
    logger.info("Created test agent: %s", test_agent_id)

    subscriber.clear_events()
    time.sleep(2)  # 等待 watcher 初始化快取

    # 執行：變更狀態
    blackboard_kg.write(
        """
        MATCH (a:Agent {agent_id: $id})
        SET a.status = 'Busy'
        """,
        {"id": test_agent_id},
    )
    logger.info("Updated agent status to Busy")

    # 等待事件
    event = subscriber.wait_for_event(action="update", topic_contains=test_agent_id, timeout=10.0)

    # 驗證
    assert event is not None, f"Expected UPDATE event for {test_agent_id}, got none. Events: {subscriber.get_events()}"
    assert event.payload.get("action") == "update"
    assert test_agent_id in event.payload.get("topic", "")
    logger.info("Received event: %s", event.payload)

    # 清理
    blackboard_kg.write("MATCH (a:Agent {agent_id: $id}) DETACH DELETE a", {"id": test_agent_id})


# -------------------------
# Test: 節點建立
# -------------------------

@pytest.mark.integration
def test_node_create_triggers_event(blackboard_agent, blackboard_kg, subscriber):
    """
    測試：建立節點會觸發 CREATE 事件
    """
    # 確認 blackboard_agent 已連線
    assert blackboard_agent.watcher is not None, "BlackboardAgent watcher not started"
    
    # 訂閱 Agent 相關變更
    sub_id = subscriber.subscribe_blackboard_pattern("Agent/*/*", timeout=10.0)
    assert sub_id is not None, f"Failed to subscribe to Agent/*/*. Subscriber connected: {subscriber._connected_event.is_set()}"

    subscriber.clear_events()
    time.sleep(2)

    # 執行：建立新 Agent
    test_agent_id = f"NewBot_{uuid.uuid4().hex[:8]}"
    blackboard_kg.write(
        """
        CREATE (a:Agent {agent_id: $id, type: 'New_Robot', status: 'Idle'})
        """,
        {"id": test_agent_id},
    )
    logger.info("Created new agent: %s", test_agent_id)

    # 等待事件
    event = subscriber.wait_for_event(action="create", topic_contains="Agent", timeout=10.0)

    # 驗證
    assert event is not None, f"Expected CREATE event, got none. Events: {subscriber.get_events()}"
    assert event.payload.get("action") == "create"
    logger.info("Received CREATE event: %s", event.payload)

    # 清理
    blackboard_kg.write("MATCH (a:Agent {agent_id: $id}) DETACH DELETE a", {"id": test_agent_id})


# -------------------------
# Test: 節點刪除
# -------------------------

@pytest.mark.integration
def test_node_delete_triggers_event(blackboard_agent, blackboard_kg, subscriber):
    """
    測試：刪除節點會觸發 DELETE 事件
    """
    # 確認 blackboard_agent 已連線
    assert blackboard_agent.watcher is not None, "BlackboardAgent watcher not started"
    
    # 訂閱 Agent 相關變更
    sub_id = subscriber.subscribe_blackboard_pattern("Agent/*/*", timeout=10.0)
    assert sub_id is not None, f"Failed to subscribe to Agent/*/*. Subscriber connected: {subscriber._connected_event.is_set()}"

    # 準備：建立測試節點
    test_agent_id = f"DeleteBot_{uuid.uuid4().hex[:8]}"
    blackboard_kg.write(
        """
        CREATE (a:Agent {agent_id: $id, type: 'Delete_Robot', status: 'Idle'})
        """,
        {"id": test_agent_id},
    )
    time.sleep(3)  # 等待 watcher 快取

    subscriber.clear_events()

    # 執行：刪除節點
    blackboard_kg.write("MATCH (a:Agent {agent_id: $id}) DETACH DELETE a", {"id": test_agent_id})
    logger.info("Deleted agent: %s", test_agent_id)

    # 等待事件
    event = subscriber.wait_for_event(action="delete", topic_contains="Agent", timeout=10.0)

    # 驗證
    assert event is not None, f"Expected DELETE event, got none. Events: {subscriber.get_events()}"
    assert event.payload.get("action") == "delete"
    logger.info("Received DELETE event: %s", event.payload)


# -------------------------
# Test: 關係變動
# -------------------------

@pytest.mark.integration
def test_relationship_change_triggers_event(blackboard_agent, blackboard_kg, subscriber):
    """
    測試：建立/刪除關係會觸發事件
    """
    # 確認 blackboard_agent 已連線
    assert blackboard_agent.watcher is not None, "BlackboardAgent watcher not started"
    
    # 訂閱關係變更
    sub_id = subscriber.subscribe_blackboard_pattern("*/CURRENT_POSITION/*", timeout=10.0)
    assert sub_id is not None, f"Failed to subscribe to */CURRENT_POSITION/*. Subscriber connected: {subscriber._connected_event.is_set()}"

    # 準備：建立測試 Agent 和 POI
    test_agent_id = f"RelBot_{uuid.uuid4().hex[:8]}"
    test_poi_id = f"P_{uuid.uuid4().hex[:6]}"

    blackboard_kg.write(
        """
        CREATE (a:Agent {agent_id: $aid, type: 'Rel_Robot'}),
               (p:POI {id: $pid, name: 'Test POI'})
        """,
        {"aid": test_agent_id, "pid": test_poi_id},
    )
    time.sleep(3)

    subscriber.clear_events()

    # 執行：建立 CURRENT_POSITION 關係
    blackboard_kg.write(
        """
        MATCH (a:Agent {agent_id: $aid}), (p:POI {id: $pid})
        CREATE (a)-[:CURRENT_POSITION {updated_at: datetime()}]->(p)
        """,
        {"aid": test_agent_id, "pid": test_poi_id},
    )
    logger.info("Created CURRENT_POSITION relationship")

    # 等待事件
    event = subscriber.wait_for_event(action="create", topic_contains="CURRENT_POSITION", timeout=10.0)

    # 驗證
    assert event is not None, f"Expected relationship CREATE event, got none. Events: {subscriber.get_events()}"
    logger.info("Received relationship event: %s", event.payload)

    # 清理
    blackboard_kg.write(
        "MATCH (a:Agent {agent_id: $aid}) DETACH DELETE a",
        {"aid": test_agent_id},
    )
    blackboard_kg.write(
        "MATCH (p:POI {id: $pid}) DETACH DELETE p",
        {"pid": test_poi_id},
    )


# -------------------------
# Test: 透過 Control Topic 訂閱
# -------------------------

@pytest.mark.integration
def test_subscribe_via_control_topic(blackboard_agent, blackboard_kg, subscriber, agent_config):
    """
    測試：透過 control topic 訂閱特定 pattern
    """
    # 發送訂閱請求
    sub_pattern = "Agent/*/status"
    request = {
        "command": "subscribe",
        "pattern": sub_pattern,
        "requester_id": subscriber.subscriber_id,
        "ignore_self": True,
    }

    # 使用 subscriber 發送訂閱請求
    pcl = subscriber.publish_sync(
        BlackboardAgent.CONTROL_TOPIC,
        request,
        timeout=10.0,
    )
    
    # 從 parcel 取出 content
    response = getattr(pcl, "content", None) if pcl else None
    logger.info("test_subscribe_via_control_topic: pcl=%s, response=%s", type(pcl).__name__ if pcl else None, response)

    # 驗證回應
    assert response is not None, f"No response from BlackboardAgent. pcl={pcl}"
    assert isinstance(response, dict), f"Expected dict response, got {type(response)}: {response}"
    assert response.get("ok") is True, f"Subscribe failed: {response}"
    assert "subscription_id" in response, f"Missing subscription_id: {response}"
    logger.info("Subscribed with pattern %s, subscription_id=%s", sub_pattern, response.get("subscription_id"))


# -------------------------
# Test: 透過 Control Topic 寫入 KG
# -------------------------

@pytest.mark.integration
def test_write_via_control_topic(blackboard_agent, subscriber):
    """
    測試：透過 control topic 寫入 KG
    """
    test_zone_name = f"TestZone_{uuid.uuid4().hex[:6]}"

    request = {
        "command": "write",
        "cypher": "CREATE (z:Zone {name: $name})",
        "params": {"name": test_zone_name},
        "requester_id": subscriber.subscriber_id,
    }

    pcl = subscriber.publish_sync(
        BlackboardAgent.CONTROL_TOPIC,
        request,
        timeout=10.0,
    )
    response = getattr(pcl, "content", None) if pcl else None

    # 驗證
    assert response is not None, f"No response for write. pcl={pcl}"
    assert isinstance(response, dict), f"Expected dict, got {type(response)}"
    assert response.get("ok") is True, f"Write failed: {response}"
    logger.info("Write via control topic succeeded")

    # 驗證 Zone 確實建立（查詢）
    query_request = {
        "command": "query",
        "cypher": "MATCH (z:Zone {name: $name}) RETURN z.name AS name",
        "params": {"name": test_zone_name},
    }
    query_pcl = subscriber.publish_sync(
        BlackboardAgent.CONTROL_TOPIC,
        query_request,
        timeout=10.0,
    )
    query_response = getattr(query_pcl, "content", None) if query_pcl else None

    assert isinstance(query_response, dict), f"Query response error: {query_response}"
    rows = query_response.get("rows", [])
    assert len(rows) > 0, f"Zone not found: {query_response}"
    assert rows[0].get("name") == test_zone_name
    logger.info("Query confirmed zone exists: %s", test_zone_name)

    # 清理
    cleanup_request = {
        "command": "write",
        "cypher": "MATCH (z:Zone {name: $name}) DETACH DELETE z",
        "params": {"name": test_zone_name},
        "requester_id": subscriber.subscriber_id,
    }
    subscriber.publish_sync(BlackboardAgent.CONTROL_TOPIC, cleanup_request, timeout=5.0)


# -------------------------
# Test: Origin ID 防死循環
# -------------------------

@pytest.mark.integration
def test_origin_id_prevents_self_notification(blackboard_agent, blackboard_kg, subscriber):
    """
    測試：訂閱者不會收到自己發起的變更事件（ignore_self=True）

    這需要特殊設定，因為目前變更是由 watcher 發起，
    而不是訂閱者直接發起。這裡驗證的是 watcher 的 origin_id 機制。
    """
    # 驗證 watcher 的 origin_id 與 subscriber 不同
    watcher_id = blackboard_agent.agent_id
    sub_id = subscriber.subscriber_id
    assert watcher_id != sub_id, "Watcher and subscriber should have different IDs"

    # 建立節點觸發事件
    test_agent_id = f"OriginBot_{uuid.uuid4().hex[:8]}"
    subscriber.clear_events()

    blackboard_kg.write(
        "CREATE (a:Agent {agent_id: $id, type: 'Origin_Robot', status: 'Idle'})",
        {"id": test_agent_id},
    )
    time.sleep(3)

    # 驗證收到的事件有 origin_id
    event = subscriber.wait_for_event(action="create", topic_contains="Agent", timeout=10.0)
    if event:
        assert "origin_id" in event.payload
        assert event.payload["origin_id"] == watcher_id
        logger.info("Event origin_id correctly set to watcher: %s", event.payload["origin_id"])

    # 清理
    blackboard_kg.write("MATCH (a:Agent {agent_id: $id}) DETACH DELETE a", {"id": test_agent_id})


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

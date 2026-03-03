# src/blackboard/agent.py
"""
BlackboardAgent：黑板代理主程式

功能：
1. 監控 Blackboard 圖譜變動（透過 BlackboardWatcher）
2. 提供 Pub/Sub 機制讓其他 Agent 訂閱/發佈變動
3. 透過 MQTT broker 將變動事件廣播給訂閱者
4. 支援精確訂閱與模糊訂閱
5. 防止死循環（Origin ID 機制）

Topic 命名空間：
- 節點屬性：<Label>/<Name>/<Property>
- 關係變動：<SourceLabel>/<Relationship>/<TargetLabel>

執行：python -m src.blackboard.agent
"""

from __future__ import annotations

from typing import Any, Callable, Optional
import uuid

from agentflow.core.agent import Agent

from src.app_helper import get_agent_config
from src.log_helper import init_logging
from src.kg.adapter_neo4j import Neo4jBoltAdapter

from .topic import BlackboardTopic, TopicPattern
from .event import BlackboardEvent, ChangeAction
from .subscription import SubscriptionManager
from .watcher import BlackboardWatcher, WatchTarget


logger = init_logging()


class BlackboardAgent(Agent):
    """
    黑板代理

    負責：
    - 監控 Blackboard KG 變動
    - 管理訂閱（精確/模糊）
    - 將變動事件發佈至 MQTT broker
    - 接收其他 Agent 的 KG 寫入請求並執行
    """

    CONTROL_TOPIC = "blackboard.control"
    EVENT_TOPIC_PREFIX = "blackboard.event"

    def __init__(
        self,
        agent_config: dict[str, Any],
        *,
        poll_interval_sec: float = 2.0,
    ):
        self.agent_config = agent_config
        self.poll_interval = poll_interval_sec

        self._kg: Optional[Neo4jBoltAdapter] = None

        self.sub_manager = SubscriptionManager()
        self.watcher: Optional[BlackboardWatcher] = None

        super().__init__("blackboard_agent.gias", agent_config)

        self._bb_agent_id = f"blackboard_agent_{self.agent_id[:8]}"

    def get_bb_agent_id(self) -> str:
        """取得 Blackboard Agent 專用 ID（用於 origin_id）"""
        return self._bb_agent_id

    @property
    def kg(self) -> Neo4jBoltAdapter:
        if self._kg is None:
            self._kg = self._build_kg_adapter()
        return self._kg

    def _build_kg_adapter(self) -> Neo4jBoltAdapter:
        """建立 Blackboard KG adapter（使用 neo4j_blackboard database）"""
        kg_cfg = self.agent_config.get("kg", {})
        base = kg_cfg.get("neo4j")
        bb_overrides = kg_cfg.get("neo4j_blackboard")
        if not isinstance(base, dict):
            raise RuntimeError("Missing [kg.neo4j] config in gias.toml")
        if not isinstance(bb_overrides, dict):
            raise RuntimeError("Missing [kg.neo4j_blackboard] config in gias.toml")
        merged = {**base, **bb_overrides}
        return Neo4jBoltAdapter.from_config(merged, logger=logger)

    def on_connected(self) -> None:
        """Agent 連線後啟動監控"""
        logger.info("BlackboardAgent connected: %s", self.agent_id)

        self.subscribe(self.CONTROL_TOPIC, "dict", self._handle_control)
        logger.info("BlackboardAgent subscribed to control topic: %s", self.CONTROL_TOPIC)

        self.watcher = BlackboardWatcher(
            kg_adapter=self.kg,
            subscription_manager=self.sub_manager,
            watcher_id=self._bb_agent_id,
            poll_interval_sec=self.poll_interval,
            logger=logger,
        )
        self.watcher.add_default_targets()

        self.sub_manager.subscribe(
            pattern="*/*/*",
            callback=self._on_any_event,
            subscriber_id=self._bb_agent_id,
            ignore_self=False,
        )

        self.watcher.start()
        logger.info("BlackboardAgent watcher started (poll_interval=%.1fs)", self.poll_interval)

    def on_disconnected(self) -> None:
        """Agent 斷線時停止監控"""
        if self.watcher:
            self.watcher.stop()
        if self._kg:
            self._kg.close()
            self._kg = None
        logger.info("BlackboardAgent disconnected: %s", self.agent_id)

    def _on_any_event(self, event: BlackboardEvent) -> None:
        """收到任何事件時，發佈至 MQTT broker"""
        mqtt_topic = f"{self.EVENT_TOPIC_PREFIX}.{event.topic.replace('/', '.')}"
        try:
            self.publish(mqtt_topic, event.to_dict())
            logger.debug("BlackboardAgent published event: %s", mqtt_topic)
        except Exception as e:
            logger.warning("BlackboardAgent publish error: %s", e)

    def _handle_control(self, topic: str, payload: Any) -> dict[str, Any]:
        """
        處理控制訊息

        支援的 command：
        - subscribe: 訂閱 pattern
        - unsubscribe: 取消訂閱
        - write: 寫入 KG（會產生變動事件）
        - query: 查詢 KG
        """
        logger.debug("BlackboardAgent._handle_control: topic=%s, payload_type=%s", 
                     topic, type(payload).__name__)
        
        # payload 可能是 parcel 物件，需要取出 content
        if hasattr(payload, "content"):
            data = payload.content if isinstance(payload.content, dict) else {}
            logger.debug("BlackboardAgent._handle_control: extracted content from parcel")
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {}
            logger.warning("BlackboardAgent._handle_control: unexpected payload type: %s", type(payload))

        command = data.get("command", "")
        requester_id = data.get("requester_id", "unknown")
        logger.debug("BlackboardAgent._handle_control: command=%s, requester_id=%s", command, requester_id)

        if command == "subscribe":
            pattern = data.get("pattern", "")
            if not pattern:
                logger.warning("BlackboardAgent: subscribe missing pattern")
                return {"ok": False, "error": "Missing 'pattern'"}
            sub_id = self.sub_manager.subscribe(
                pattern=pattern,
                callback=lambda e: self._forward_to_requester(e, requester_id),
                subscriber_id=requester_id,
                ignore_self=data.get("ignore_self", True),
            )
            logger.info("BlackboardAgent: %s subscribed to %s, sub_id=%s", requester_id, pattern, sub_id)
            result = {"ok": True, "subscription_id": sub_id}
            logger.debug("BlackboardAgent._handle_control returning: %s", result)
            return result

        if command == "unsubscribe":
            sub_id = data.get("subscription_id", "")
            if sub_id:
                ok = self.sub_manager.unsubscribe(sub_id)
            else:
                count = self.sub_manager.unsubscribe_all(requester_id)
                ok = count > 0
            return {"ok": ok}

        if command == "write":
            return self._handle_write(data, requester_id)

        if command == "query":
            return self._handle_query(data)

        if command == "list_subscriptions":
            subs = self.sub_manager.get_subscriptions(requester_id if data.get("mine_only") else None)
            return {"ok": True, "subscriptions": subs}

        if command == "observe":
            return self._handle_observe(data, requester_id)

        return {"ok": False, "error": f"Unknown command: {command}"}

    def _forward_to_requester(self, event: BlackboardEvent, requester_id: str) -> None:
        """將事件轉發給特定 requester（透過 MQTT topic）"""
        mqtt_topic = f"blackboard.subscriber.{requester_id}"
        try:
            self.publish(mqtt_topic, event.to_dict())
        except Exception as e:
            logger.warning("BlackboardAgent forward error to %s: %s", requester_id, e)

    def _handle_write(self, data: dict[str, Any], origin_id: str) -> dict[str, Any]:
        """
        處理 KG 寫入請求

        寫入後會由 Watcher 偵測到變動並產生事件，
        事件的 origin_id 會是 watcher_id，但我們可在 metadata 中保留原始 requester
        """
        cypher = data.get("cypher", "")
        params = data.get("params", {})
        if not cypher:
            return {"ok": False, "error": "Missing 'cypher'"}

        try:
            result = self.kg.write(cypher, params)
            logger.info("BlackboardAgent write by %s: %s", origin_id, cypher[:100])
            return {"ok": True, "result": result}
        except Exception as e:
            logger.warning("BlackboardAgent write error: %s", e)
            return {"ok": False, "error": str(e)}

    def _handle_query(self, data: dict[str, Any]) -> dict[str, Any]:
        """處理 KG 查詢請求"""
        cypher = data.get("cypher", "")
        params = data.get("params", {})
        if not cypher:
            return {"ok": False, "error": "Missing 'cypher'"}

        try:
            rows = self.kg.query(cypher, params)
            return {"ok": True, "rows": rows}
        except Exception as e:
            logger.warning("BlackboardAgent query error: %s", e)
            return {"ok": False, "error": str(e)}

    def _handle_observe(self, data: dict[str, Any], observer_id: str) -> dict[str, Any]:
        """
        處理觀察者提交的觀察結果
        
        將觀察結果轉換為 KG 節點/關係並寫入，
        同時發佈對應的變動事件。
        """
        observation = data.get("observation")
        if not observation or not isinstance(observation, dict):
            return {"ok": False, "error": "Missing or invalid 'observation'"}

        obs_id = observation.get("observation_id", "")
        obs_type = observation.get("observation_type", "unknown")
        saliency = observation.get("saliency", 1)
        entities = observation.get("entities", [])
        relations = observation.get("relations", [])
        raw_description = observation.get("raw_description", "")
        metadata = observation.get("metadata", {})

        try:
            cypher = """
            MERGE (o:Observation {observation_id: $obs_id})
            SET o.observer_id = $observer_id,
                o.observation_type = $obs_type,
                o.saliency = $saliency,
                o.raw_description = $raw_description,
                o.timestamp = $timestamp,
                o.confidence = $confidence,
                o.entity_count = $entity_count,
                o.relation_count = $relation_count
            RETURN o
            """
            params = {
                "obs_id": obs_id,
                "observer_id": observer_id,
                "obs_type": obs_type,
                "saliency": saliency,
                "raw_description": raw_description,
                "timestamp": observation.get("timestamp", ""),
                "confidence": observation.get("confidence", 1.0),
                "entity_count": len(entities),
                "relation_count": len(relations),
            }
            self.kg.write(cypher, params)

            for entity in entities:
                entity_cypher = """
                MATCH (o:Observation {observation_id: $obs_id})
                MERGE (e:ObservedEntity {entity_id: $entity_id})
                SET e.entity_type = $entity_type,
                    e.label = $label,
                    e.confidence = $confidence,
                    e.properties = $properties
                MERGE (o)-[:OBSERVED]->(e)
                """
                entity_params = {
                    "obs_id": obs_id,
                    "entity_id": entity.get("entity_id") or f"{obs_id}_{entity.get('label', 'unknown')}",
                    "entity_type": entity.get("entity_type", "Unknown"),
                    "label": entity.get("label", ""),
                    "confidence": entity.get("confidence", 1.0),
                    "properties": str(entity.get("properties", {})),
                }
                self.kg.write(entity_cypher, entity_params)

            self.publish_change(
                topic=f"Observation/{observer_id}/{obs_type}",
                action=ChangeAction.CREATE,
                new_value={
                    "observation_id": obs_id,
                    "saliency": saliency,
                    "entity_count": len(entities),
                    "raw_description": raw_description[:100],
                },
                metadata={
                    "observer_id": observer_id,
                    "observation_type": obs_type,
                    **metadata,
                },
            )

            logger.info("BlackboardAgent received observation from %s: type=%s, saliency=%d, entities=%d",
                       observer_id, obs_type, saliency, len(entities))
            
            return {
                "ok": True,
                "observation_id": obs_id,
                "entities_stored": len(entities),
            }

        except Exception as e:
            logger.warning("BlackboardAgent observe error: %s", e)
            return {"ok": False, "error": str(e)}

    def publish_change(
        self,
        *,
        topic: str,
        action: ChangeAction,
        old_value: Any = None,
        new_value: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        手動發佈變動事件（供其他模組使用）

        若是透過 _handle_write 寫入 KG，Watcher 會自動偵測並發佈，
        此方法用於需要手動觸發事件的場景
        """
        event = BlackboardEvent.create(
            topic=topic,
            action=action,
            old_value=old_value,
            new_value=new_value,
            origin_id=self._bb_agent_id,
            metadata=metadata,
        )
        self.sub_manager.dispatch(event)


def main() -> None:
    agent_config = get_agent_config()
    bb_cfg = agent_config.get("blackboard", {})
    poll_interval = bb_cfg.get("poll_interval_sec", 2.0)

    agent = BlackboardAgent(
        agent_config=agent_config,
        poll_interval_sec=poll_interval,
    )
    print(f"\n=== BlackboardAgent starting: {agent.agent_id} ===")
    print(f"  poll_interval: {poll_interval}s")
    print(f"  control_topic: {BlackboardAgent.CONTROL_TOPIC}")
    print(f"  event_topic_prefix: {BlackboardAgent.EVENT_TOPIC_PREFIX}")
    print()

    agent.start_thread()

    from src.app_helper import wait_agent
    wait_agent(agent)


if __name__ == "__main__":
    main()

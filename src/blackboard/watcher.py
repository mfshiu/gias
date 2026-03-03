# src/blackboard/watcher.py
"""
BlackboardWatcher：監控 KG（Blackboard）變動並觸發事件

實作方式：
1. 輪詢（Polling）：定期查詢 KG 狀態，比較差異
2. 支援節點屬性變動與關係變動偵測
3. 產生 BlackboardEvent 並交給 SubscriptionManager 分發

NOTE:
- Neo4j Community 不支援 Change Data Capture (CDC)，故採輪詢方式
- 若未來升級 Neo4j Enterprise，可改用 CDC 或 Transaction Event Handler
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional
from dataclasses import dataclass, field

from .topic import BlackboardTopic, TopicType
from .event import BlackboardEvent, ChangeAction
from .subscription import SubscriptionManager


@dataclass
class WatchTarget:
    """監控目標"""
    label: str
    id_property: str = "id"
    watch_properties: list[str] = field(default_factory=list)
    watch_relationships: list[str] = field(default_factory=list)


class BlackboardWatcher:
    """
    黑板監控器

    定期輪詢 KG，偵測變動並產生事件
    """

    def __init__(
        self,
        kg_adapter,
        subscription_manager: SubscriptionManager,
        *,
        watcher_id: str = "blackboard_watcher",
        poll_interval_sec: float = 2.0,
        logger: Any = None,
    ):
        self.kg = kg_adapter
        self.sub_manager = subscription_manager
        self.watcher_id = watcher_id
        self.poll_interval = poll_interval_sec
        self.logger = logger

        self._watch_targets: list[WatchTarget] = []
        self._state_cache: dict[str, dict[str, Any]] = {}
        self._rel_cache: dict[str, dict[str, Any]] = {}

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    def add_watch_target(self, target: WatchTarget) -> None:
        with self._lock:
            self._watch_targets.append(target)

    def add_default_targets(self) -> None:
        """加入 Blackboard 常見監控目標"""
        self.add_watch_target(WatchTarget(
            label="Agent",
            id_property="agent_id",
            watch_properties=["status", "battery", "task_id"],
            watch_relationships=["CURRENT_POSITION", "CURRENT_STATE", "ASSIGNED_TASK"],
        ))
        self.add_watch_target(WatchTarget(
            label="Zone",
            id_property="name",
            watch_properties=["crowd_level", "alert"],
            watch_relationships=["CURRENT_STATE"],
        ))
        self.add_watch_target(WatchTarget(
            label="Task",
            id_property="task_id",
            watch_properties=["priority", "status"],
            watch_relationships=["HAS_STATUS", "ASSIGNED_TO"],
        ))
        self.add_watch_target(WatchTarget(
            label="POI",
            id_property="id",
            watch_properties=["status", "occupancy"],
            watch_relationships=[],
        ))
        self.add_watch_target(WatchTarget(
            label="Booth",
            id_property="id",
            watch_properties=["status", "visitors"],
            watch_relationships=[],
        ))

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._init_cache()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        if self.logger:
            self.logger.info("BlackboardWatcher started (interval=%.1fs)", self.poll_interval)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self.logger:
            self.logger.info("BlackboardWatcher stopped")

    def _poll_loop(self) -> None:
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                if self.logger:
                    self.logger.warning("BlackboardWatcher poll error: %s", e)
            time.sleep(self.poll_interval)

    def _init_cache(self) -> None:
        """初始化快取，取得初始狀態"""
        with self._lock:
            for target in self._watch_targets:
                self._cache_nodes(target)
                self._cache_relationships(target)

    def _poll_once(self) -> None:
        """執行一次輪詢，偵測變動"""
        with self._lock:
            targets = list(self._watch_targets)

        for target in targets:
            self._detect_node_changes(target)
            self._detect_relationship_changes(target)

    def _cache_nodes(self, target: WatchTarget) -> None:
        """快取指定 Label 的所有節點狀態"""
        props = ", ".join([f"n.{p} AS {p}" for p in target.watch_properties]) if target.watch_properties else ""
        cypher = f"""
        MATCH (n:{target.label})
        RETURN n.{target.id_property} AS node_id, id(n) AS neo4j_id
        {', ' + props if props else ''}
        """
        rows = self.kg.query(cypher, {})
        for row in rows:
            node_id = row.get("node_id")
            if node_id:
                cache_key = f"{target.label}:{node_id}"
                self._state_cache[cache_key] = dict(row)

    def _cache_relationships(self, target: WatchTarget) -> None:
        """快取指定 Label 的關係"""
        for rel_type in target.watch_relationships:
            cypher = f"""
            MATCH (n:{target.label})-[r:{rel_type}]->(m)
            RETURN n.{target.id_property} AS source_id, type(r) AS rel_type,
                   labels(m)[0] AS target_label,
                   coalesce(m.{target.id_property}, m.id, m.name, m.status_name) AS target_id,
                   id(r) AS rel_id
            """
            rows = self.kg.query(cypher, {})
            for row in rows:
                source_id = row.get("source_id")
                target_id = row.get("target_id")
                if source_id and target_id:
                    cache_key = f"{target.label}:{source_id}:{rel_type}:{target_id}"
                    self._rel_cache[cache_key] = dict(row)

    def _detect_node_changes(self, target: WatchTarget) -> None:
        """偵測節點屬性變動"""
        props = ", ".join([f"n.{p} AS {p}" for p in target.watch_properties]) if target.watch_properties else ""
        cypher = f"""
        MATCH (n:{target.label})
        RETURN n.{target.id_property} AS node_id, id(n) AS neo4j_id
        {', ' + props if props else ''}
        """
        rows = self.kg.query(cypher, {})
        current_ids = set()

        for row in rows:
            node_id = row.get("node_id")
            if not node_id:
                continue
            current_ids.add(node_id)
            cache_key = f"{target.label}:{node_id}"
            old_state = self._state_cache.get(cache_key)

            if old_state is None:
                topic = f"{target.label}/{node_id}/*"
                event = BlackboardEvent.create(
                    topic=topic,
                    action=ChangeAction.CREATE,
                    old_value=None,
                    new_value=dict(row),
                    origin_id=self.watcher_id,
                    metadata={"label": target.label, "node_id": node_id},
                )
                self.sub_manager.dispatch(event)
                self._state_cache[cache_key] = dict(row)
            else:
                for prop in target.watch_properties:
                    old_val = old_state.get(prop)
                    new_val = row.get(prop)
                    if old_val != new_val:
                        topic = f"{target.label}/{node_id}/{prop}"
                        event = BlackboardEvent.create(
                            topic=topic,
                            action=ChangeAction.UPDATE,
                            old_value=old_val,
                            new_value=new_val,
                            origin_id=self.watcher_id,
                            metadata={"label": target.label, "node_id": node_id, "property": prop},
                        )
                        self.sub_manager.dispatch(event)
                self._state_cache[cache_key] = dict(row)

        cached_keys = [k for k in self._state_cache if k.startswith(f"{target.label}:")]
        for cache_key in cached_keys:
            node_id = cache_key.split(":", 1)[1]
            if node_id not in current_ids:
                old_state = self._state_cache.pop(cache_key, None)
                if old_state:
                    topic = f"{target.label}/{node_id}/*"
                    event = BlackboardEvent.create(
                        topic=topic,
                        action=ChangeAction.DELETE,
                        old_value=old_state,
                        new_value=None,
                        origin_id=self.watcher_id,
                        metadata={"label": target.label, "node_id": node_id},
                    )
                    self.sub_manager.dispatch(event)

    def _detect_relationship_changes(self, target: WatchTarget) -> None:
        """偵測關係變動"""
        for rel_type in target.watch_relationships:
            cypher = f"""
            MATCH (n:{target.label})-[r:{rel_type}]->(m)
            RETURN n.{target.id_property} AS source_id, type(r) AS rel_type,
                   labels(m)[0] AS target_label,
                   coalesce(m.{target.id_property}, m.id, m.name, m.status_name) AS target_id,
                   id(r) AS rel_id
            """
            rows = self.kg.query(cypher, {})
            current_keys = set()

            for row in rows:
                source_id = row.get("source_id")
                target_id = row.get("target_id")
                target_label = row.get("target_label", "")
                if not source_id or not target_id:
                    continue
                cache_key = f"{target.label}:{source_id}:{rel_type}:{target_id}"
                current_keys.add(cache_key)

                if cache_key not in self._rel_cache:
                    topic = f"{target.label}/{rel_type}/{target_label}"
                    event = BlackboardEvent.create(
                        topic=topic,
                        action=ChangeAction.CREATE,
                        old_value=None,
                        new_value={"source_id": source_id, "target_id": target_id},
                        origin_id=self.watcher_id,
                        metadata={
                            "source_label": target.label,
                            "source_id": source_id,
                            "rel_type": rel_type,
                            "target_label": target_label,
                            "target_id": target_id,
                        },
                    )
                    self.sub_manager.dispatch(event)
                    self._rel_cache[cache_key] = dict(row)

            prefix = f"{target.label}:"
            suffix = f":{rel_type}:"
            cached_keys = [k for k in self._rel_cache if k.startswith(prefix) and suffix in k]
            for cache_key in cached_keys:
                if cache_key not in current_keys:
                    old_rel = self._rel_cache.pop(cache_key, None)
                    if old_rel:
                        target_label = old_rel.get("target_label", "")
                        topic = f"{target.label}/{rel_type}/{target_label}"
                        event = BlackboardEvent.create(
                            topic=topic,
                            action=ChangeAction.DELETE,
                            old_value=old_rel,
                            new_value=None,
                            origin_id=self.watcher_id,
                            metadata=old_rel,
                        )
                        self.sub_manager.dispatch(event)

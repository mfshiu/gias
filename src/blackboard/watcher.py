# src/blackboard/watcher.py
"""
BlackboardWatcher：監控 KG（Blackboard）變動並觸發事件

實作方式：
1. 輪詢（Polling）：定期查詢 KG 狀態，比較差異；每一輪所有監控目標合成一次查詢
2. 寫入觸發：經 BlackboardAgent 寫入後呼叫 notify_change()，立即偵測，不必等下一輪
3. 只掃描有人訂閱的 Label（only_subscribed=True）；開始被訂閱時先建立快取（不發事件），
   之後的變動才會通知
4. 支援節點屬性變動與關係變動偵測，產生 BlackboardEvent 並交給 SubscriptionManager 分發

NOTE:
- Neo4j Community 不支援 Change Data Capture (CDC)，故採輪詢方式；直接寫入 Neo4j（不經
  BlackboardAgent）的變動仍要等下一輪輪詢（poll_interval_sec）才會偵測到
- 若未來升級 Neo4j Enterprise，可改用 CDC 或 Transaction Event Handler
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional
from dataclasses import dataclass, field

from .event import BlackboardEvent, ChangeAction
from .subscription import SubscriptionManager


@dataclass
class WatchTarget:
    """監控目標"""
    label: str
    id_property: str = "id"
    watch_properties: list[str] = field(default_factory=list)
    watch_relationships: list[str] = field(default_factory=list)


def _q(name: str) -> str:
    """Cypher 識別字（Label、屬性、關係型別）加上反引號。"""
    return "`" + str(name).replace("`", "``") + "`"


def _lit(text: str) -> str:
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_poll_query(targets: list[WatchTarget]) -> str:
    """一次查詢取得所有監控目標的節點屬性與關係（UNION ALL，欄位一致）。"""
    parts: list[str] = []
    for t in targets:
        props = "{" + ", ".join(f"{_q(p)}: n.{_q(p)}" for p in t.watch_properties) + "}"
        parts.append(
            f"MATCH (n:{_q(t.label)}) "
            f"RETURN 'node' AS kind, {_lit(t.label)} AS label, n.{_q(t.id_property)} AS node_id, "
            f"id(n) AS neo4j_id, {props} AS props, null AS rel_type, null AS target_label, "
            f"null AS target_id, null AS rel_id"
        )
        for rel_type in t.watch_relationships:
            parts.append(
                f"MATCH (n:{_q(t.label)})-[r:{_q(rel_type)}]->(m) "
                f"RETURN 'rel' AS kind, {_lit(t.label)} AS label, n.{_q(t.id_property)} AS node_id, "
                f"null AS neo4j_id, null AS props, type(r) AS rel_type, labels(m)[0] AS target_label, "
                f"coalesce(m.{_q(t.id_property)}, m.id, m.name, m.status_name) AS target_id, id(r) AS rel_id"
            )
    return "\nUNION ALL\n".join(parts)


class BlackboardWatcher:
    """
    黑板監控器

    定期輪詢 KG（或收到寫入通知時立即輪詢），偵測變動並產生事件
    """

    def __init__(
        self,
        kg_adapter,
        subscription_manager: SubscriptionManager,
        *,
        watcher_id: str = "blackboard_watcher",
        poll_interval_sec: float = 2.0,
        min_poll_gap_sec: float = 0.1,
        only_subscribed: bool = False,
        logger: Any = None,
    ):
        """
        poll_interval_sec：沒有寫入通知時的輪詢週期（偵測直接寫入 Neo4j 的變動）
        min_poll_gap_sec：寫入通知觸發輪詢時，與上一輪至少間隔多久（連續寫入合併成一輪）
        only_subscribed：只掃描有人訂閱的 Label
        """
        self.kg = kg_adapter
        self.sub_manager = subscription_manager
        self.watcher_id = watcher_id
        self.poll_interval = poll_interval_sec
        self.min_poll_gap = min_poll_gap_sec
        self.only_subscribed = only_subscribed
        self.logger = logger

        self._watch_targets: list[WatchTarget] = []
        self._state_cache: dict[str, dict[str, Any]] = {}
        self._rel_cache: dict[str, dict[str, Any]] = {}
        self._primed: set[str] = set()          # 已建立快取的 Label

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._wake = threading.Event()

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
        self._wake.clear()
        self._init_cache()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        if self.logger:
            self.logger.info("BlackboardWatcher started (interval=%.1fs)", self.poll_interval)

    def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self.logger:
            self.logger.info("BlackboardWatcher stopped")

    def notify_change(self) -> None:
        """有寫入（經 BlackboardAgent）時呼叫：盡快輪詢一次，不必等下一個週期。"""
        self._wake.set()

    def prime_watched(self) -> None:
        """為新被訂閱的 Label 建立快取（不發事件）；訂閱生效後的變動才會通知。"""
        with self._lock:
            targets = [t for t in self._watched_targets() if t.label not in self._primed]
            if targets:
                self._apply(targets, self._fetch(targets), dispatch=False)

    def _poll_loop(self) -> None:
        last_poll = 0.0
        while self._running:
            if time.monotonic() - last_poll >= self.min_poll_gap:
                last_poll = time.monotonic()
                try:
                    self._poll_once()
                except Exception as e:
                    if self.logger:
                        self.logger.warning("BlackboardWatcher poll error: %s", e)
            woke = self._wake.wait(self.poll_interval)
            self._wake.clear()
            if woke and self._running:
                # 連續寫入合併成一輪：與上一輪至少間隔 min_poll_gap
                remaining = self.min_poll_gap - (time.monotonic() - last_poll)
                if remaining > 0:
                    time.sleep(remaining)

    def _watched_targets(self) -> list[WatchTarget]:
        if not self.only_subscribed:
            return list(self._watch_targets)
        return [t for t in self._watch_targets if self.sub_manager.watches_label(t.label)]

    def _init_cache(self) -> None:
        """初始化快取，取得初始狀態"""
        with self._lock:
            self._primed.clear()
            self._state_cache.clear()
            self._rel_cache.clear()
        self.prime_watched()

    def _poll_once(self) -> None:
        """執行一次輪詢（一次查詢），偵測變動"""
        with self._lock:
            watched = self._watched_targets()
            # 不再被訂閱的 Label：丟掉快取，再被訂閱時重新建立
            watched_labels = {t.label for t in watched}
            for label in self._primed - watched_labels:
                self._drop_cache(label)
            if not watched:
                return
            rows = self._fetch(watched)
            fresh = [t for t in watched if t.label not in self._primed]
            if fresh:
                self._apply(fresh, rows, dispatch=False)
            self._apply([t for t in watched if t not in fresh], rows, dispatch=True)

    def _fetch(self, targets: list[WatchTarget]) -> list[dict[str, Any]]:
        if not targets:
            return []
        return self.kg.query(build_poll_query(targets), {}) or []

    def _apply(self, targets: list[WatchTarget], rows: list[dict[str, Any]], *, dispatch: bool) -> None:
        """依查詢結果更新快取；dispatch 為 True 時對差異發出事件。"""
        nodes: dict[str, list[dict[str, Any]]] = {t.label: [] for t in targets}
        rels: dict[tuple[str, str], list[dict[str, Any]]] = {
            (t.label, r): [] for t in targets for r in t.watch_relationships
        }
        for row in rows:
            label = row.get("label")
            if row.get("kind") == "node" and label in nodes:
                nodes[label].append({"node_id": row.get("node_id"), "neo4j_id": row.get("neo4j_id"),
                                     **(row.get("props") or {})})
            elif row.get("kind") == "rel" and (label, row.get("rel_type")) in rels:
                rels[(label, row.get("rel_type"))].append({
                    "source_id": row.get("node_id"), "rel_type": row.get("rel_type"),
                    "target_label": row.get("target_label"), "target_id": row.get("target_id"),
                    "rel_id": row.get("rel_id"),
                })
        for t in targets:
            self._detect_node_changes(t, nodes[t.label], dispatch=dispatch)
            for rel_type in t.watch_relationships:
                self._detect_relationship_changes(t, rel_type, rels[(t.label, rel_type)], dispatch=dispatch)
            self._primed.add(t.label)

    def _drop_cache(self, label: str) -> None:
        prefix = f"{label}:"
        for cache in (self._state_cache, self._rel_cache):
            for key in [k for k in cache if k.startswith(prefix)]:
                del cache[key]
        self._primed.discard(label)

    def _dispatch(self, event: BlackboardEvent, dispatch: bool) -> None:
        if dispatch:
            self.sub_manager.dispatch(event)

    def _detect_node_changes(self, target: WatchTarget, rows: list[dict[str, Any]], *, dispatch: bool = True) -> None:
        """偵測節點屬性變動"""
        current_ids = set()

        for row in rows:
            node_id = row.get("node_id")
            if not node_id:
                continue
            current_ids.add(node_id)
            cache_key = f"{target.label}:{node_id}"
            old_state = self._state_cache.get(cache_key)

            if old_state is None:
                self._dispatch(BlackboardEvent.create(
                    topic=f"{target.label}/{node_id}/*",
                    action=ChangeAction.CREATE,
                    old_value=None,
                    new_value=dict(row),
                    origin_id=self.watcher_id,
                    metadata={"label": target.label, "node_id": node_id},
                ), dispatch)
            else:
                for prop in target.watch_properties:
                    old_val = old_state.get(prop)
                    new_val = row.get(prop)
                    if old_val != new_val:
                        self._dispatch(BlackboardEvent.create(
                            topic=f"{target.label}/{node_id}/{prop}",
                            action=ChangeAction.UPDATE,
                            old_value=old_val,
                            new_value=new_val,
                            origin_id=self.watcher_id,
                            metadata={"label": target.label, "node_id": node_id, "property": prop},
                        ), dispatch)
            self._state_cache[cache_key] = dict(row)

        cached_keys = [k for k in self._state_cache if k.startswith(f"{target.label}:")]
        for cache_key in cached_keys:
            node_id = cache_key.split(":", 1)[1]
            if node_id not in current_ids:
                old_state = self._state_cache.pop(cache_key, None)
                if old_state:
                    self._dispatch(BlackboardEvent.create(
                        topic=f"{target.label}/{node_id}/*",
                        action=ChangeAction.DELETE,
                        old_value=old_state,
                        new_value=None,
                        origin_id=self.watcher_id,
                        metadata={"label": target.label, "node_id": node_id},
                    ), dispatch)

    def _detect_relationship_changes(
        self, target: WatchTarget, rel_type: str, rows: list[dict[str, Any]], *, dispatch: bool = True
    ) -> None:
        """偵測關係變動"""
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
                self._dispatch(BlackboardEvent.create(
                    topic=f"{target.label}/{rel_type}/{target_label}",
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
                ), dispatch)
                self._rel_cache[cache_key] = dict(row)

        prefix = f"{target.label}:"
        suffix = f":{rel_type}:"
        cached_keys = [k for k in self._rel_cache if k.startswith(prefix) and suffix in k]
        for cache_key in cached_keys:
            if cache_key not in current_keys:
                old_rel = self._rel_cache.pop(cache_key, None)
                if old_rel:
                    target_label = old_rel.get("target_label", "")
                    self._dispatch(BlackboardEvent.create(
                        topic=f"{target.label}/{rel_type}/{target_label}",
                        action=ChangeAction.DELETE,
                        old_value=old_rel,
                        new_value=None,
                        origin_id=self.watcher_id,
                        metadata=old_rel,
                    ), dispatch)

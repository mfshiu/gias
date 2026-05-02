"""
動態事件注入器

對 Blackboard KG 注入：
  - crowd_congestion : 將某 Zone 的 CURRENT_STATE 改為 Crowded
  - area_closure     : 將某 Zone 的 CURRENT_STATE 改為 Closed
  - route_detour     : 將某 CONNECTED_TO 邊標記 blocked=true（雙向）

依事件注入比例 ratio (0.0~1.0) 決定本次是否實際注入。
事件可在「任務開始前」或「任務執行中」觸發。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from src.kg.adapter_neo4j import Neo4jBoltAdapter
from experiment1.config import (
    EVENT_KIND_CROWD,
    EVENT_KIND_CLOSURE,
    EVENT_KIND_DETOUR,
    STATE_CROWDED,
    STATE_CLOSED,
    STATE_NORMAL,
    ZONES,
    EDGES,
)


@dataclass
class InjectedEvent:
    kind: str  # crowd_congestion / area_closure / route_detour
    target: str  # zone name 或 "edge:a→b"
    detail: dict


class EventInjector:
    def __init__(
        self,
        kg: Neo4jBoltAdapter,
        *,
        ratio: float = 0.0,
        rng: Optional[random.Random] = None,
        allowed_kinds: Optional[tuple[str, ...]] = None,
    ):
        self.kg = kg
        self.ratio = max(0.0, min(1.0, float(ratio)))
        self.rng = rng or random.Random()
        self.allowed_kinds = allowed_kinds or (
            EVENT_KIND_CROWD,
            EVENT_KIND_CLOSURE,
            EVENT_KIND_DETOUR,
        )

    # ------------------------------------------------------------------
    # 概率閘門
    # ------------------------------------------------------------------
    def should_inject(self) -> bool:
        if self.ratio <= 0.0:
            return False
        return self.rng.random() < self.ratio

    # ------------------------------------------------------------------
    # 注入單一事件（避開使用者目標 zone 與保護節點）
    # ------------------------------------------------------------------
    def inject_one(
        self,
        *,
        exclude_zones: tuple[str, ...] = (),
        protected_nodes: tuple[str, ...] = (),
    ) -> Optional[InjectedEvent]:
        """
        protected_nodes：detour 不會封鎖至少有一端是這些節點的邊
        （避免起點/終點被孤立）。
        """
        if not self.should_inject():
            return None
        kind = self.rng.choice(self.allowed_kinds)
        return self._do_inject(kind, exclude_zones=exclude_zones, protected_nodes=protected_nodes)

    def _do_inject(
        self,
        kind: str,
        *,
        exclude_zones: tuple[str, ...],
        protected_nodes: tuple[str, ...] = (),
    ) -> Optional[InjectedEvent]:
        if kind == EVENT_KIND_CROWD:
            return self._inject_crowd(exclude_zones)
        if kind == EVENT_KIND_CLOSURE:
            return self._inject_closure(exclude_zones)
        if kind == EVENT_KIND_DETOUR:
            return self._inject_detour(protected_nodes)
        return None

    # ------------------------------------------------------------------
    # 三類事件的具體寫入
    # ------------------------------------------------------------------
    def _candidate_zones(self, exclude: tuple[str, ...]) -> list[str]:
        return [z for z in ZONES if z not in exclude and z != "Main_Hall"]

    def _set_zone_state(self, zone: str, state: str) -> None:
        self.kg.write(
            """
            MATCH (z:Zone {name: $zone})
            OPTIONAL MATCH (z)-[old:CURRENT_STATE]->()
            DELETE old
            WITH z
            MATCH (st:State {status_name: $state})
            CREATE (z)-[:CURRENT_STATE {updated_at: datetime()}]->(st)
            """,
            {"zone": zone, "state": state},
        )

    def _inject_crowd(self, exclude: tuple[str, ...]) -> Optional[InjectedEvent]:
        cands = self._candidate_zones(exclude)
        if not cands:
            return None
        zone = self.rng.choice(cands)
        self._set_zone_state(zone, STATE_CROWDED)
        return InjectedEvent(EVENT_KIND_CROWD, zone, {"new_state": STATE_CROWDED})

    def _inject_closure(self, exclude: tuple[str, ...]) -> Optional[InjectedEvent]:
        cands = self._candidate_zones(exclude)
        if not cands:
            return None
        zone = self.rng.choice(cands)
        self._set_zone_state(zone, STATE_CLOSED)
        return InjectedEvent(EVENT_KIND_CLOSURE, zone, {"new_state": STATE_CLOSED})

    def _inject_detour(self, protected: tuple[str, ...]) -> Optional[InjectedEvent]:
        # 跳過任一端為 protected_nodes 的邊（保護起點/終點 anchor 不被孤立）
        protected_set = set(protected or ())
        candidates = [(a, b, d) for (a, b, d) in EDGES if a not in protected_set and b not in protected_set]
        if not candidates:
            return None
        self.rng.shuffle(candidates)
        for a, b, dist in candidates:
            self.kg.write(
                """
                MATCH (a)-[r:CONNECTED_TO]->(b)
                WHERE a.id = $a AND b.id = $b
                SET r.blocked = true
                """,
                {"a": a, "b": b},
            )
            self.kg.write(
                """
                MATCH (b)-[r:CONNECTED_TO]->(a)
                WHERE a.id = $a AND b.id = $b
                SET r.blocked = true
                """,
                {"a": a, "b": b},
            )
            return InjectedEvent(
                EVENT_KIND_DETOUR,
                f"edge:{a}->{b}",
                {"a": a, "b": b, "distance": dist, "blocked": True},
            )
        return None

    # ------------------------------------------------------------------
    # Reset：把所有 Zone 狀態回到 Normal、所有 edge 解鎖
    # ------------------------------------------------------------------
    def reset_all(self) -> None:
        for z in ZONES:
            self._set_zone_state(z, STATE_NORMAL)
        self.kg.write(
            """
            MATCH ()-[r:CONNECTED_TO]->()
            SET r.blocked = false
            """,
            {},
        )

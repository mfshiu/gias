# src/blackboard/projection.py
"""
觀察投影：把觀察者回報的實體同步到黑板上的領域節點（例：人流觀察 → 更新 Zone 的人潮狀態）。

BlackboardAgent 收到 observe 時，除了記錄 Observation / ObservedEntity，還會依投影規則更新領域節點，
供其他代理查詢。規則依領域而定，核心程式不寫死任何領域：

- ObservationProjection(observation_type, entity_type, build)：build(entity) 回傳 (cypher, params)，
  或 None 表示這筆實體資料不足、略過
- 預設集以名稱登記在 PROJECTION_PRESETS（內建 "venue"：展場的 Zone / Booth）；
  BlackboardAgent 依 [blackboard] projections（預設集名稱清單，未設定為 ["venue"]）或建構參數 projections 取得規則
- 其他領域：register_projection_preset("my_domain", [...]) 後在設定中指定，或直接傳入 projections
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

Statement = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class ObservationProjection:
    observation_type: str
    entity_type: str
    build: Callable[[dict[str, Any]], Statement | None]

    def applies_to(self, observation_type: str, entity: dict[str, Any]) -> bool:
        return self.observation_type == observation_type and self.entity_type == entity.get("entity_type")


def _props(entity: dict[str, Any]) -> dict[str, Any]:
    props = entity.get("properties")
    return props if isinstance(props, dict) else {}


# ----------------------------------------------------------------------
# 展場（venue）：人流觀察更新 Zone 的 CURRENT_STATE、設施事件更新 Booth 的 status
# ----------------------------------------------------------------------
def _zone_crowd(entity: dict[str, Any]) -> Statement | None:
    zone_name = entity.get("label") or str(entity.get("entity_id") or "").replace("zone_", "")
    crowd_level = _props(entity).get("crowd_level")
    if not zone_name or not crowd_level:
        return None
    return (
        """
        MATCH (z:Zone {name: $zone_name})
        OPTIONAL MATCH (z)-[r:CURRENT_STATE]->()
        DELETE r
        WITH z
        MATCH (st:State {status_name: $crowd_level})
        CREATE (z)-[:CURRENT_STATE {updated_at: datetime()}]->(st)
        """,
        {"zone_name": zone_name, "crowd_level": crowd_level},
    )


def _booth_status(entity: dict[str, Any]) -> Statement | None:
    booth_id = entity.get("entity_id")
    status = _props(entity).get("status")
    if not booth_id or not status:
        return None
    return (
        """
        MATCH (b:Booth {id: $booth_id})
        SET b.status = $status, b.updated_at = datetime()
        """,
        {"booth_id": booth_id, "status": status},
    )


VENUE_PROJECTIONS: tuple[ObservationProjection, ...] = (
    ObservationProjection("pedestrian_flow", "Zone", _zone_crowd),
    ObservationProjection("facility_event", "Booth", _booth_status),
)

PROJECTION_PRESETS: dict[str, tuple[ObservationProjection, ...]] = {"venue": VENUE_PROJECTIONS}
DEFAULT_PRESETS: tuple[str, ...] = ("venue",)


def register_projection_preset(name: str, projections: Iterable[ObservationProjection]) -> None:
    PROJECTION_PRESETS[name] = tuple(projections)


def projections_from_config(names: Any) -> tuple[ObservationProjection, ...]:
    """[blackboard] projections → 規則；None（未設定）用預設集，空清單表示不投影。未知名稱拋出 ValueError。"""
    if names is None:
        names = DEFAULT_PRESETS
    if isinstance(names, str):
        names = [names]
    rules: list[ObservationProjection] = []
    for name in names:
        if name not in PROJECTION_PRESETS:
            raise ValueError(f"Unknown blackboard projection preset {name!r}; known: {sorted(PROJECTION_PRESETS)}")
        rules.extend(PROJECTION_PRESETS[name])
    return tuple(rules)

"""
人流／動線感測器代理

模擬：各 Zone 人潮等級（Crowded / Normal / Sparse）、密度與動線方向。
以 `write` 命令直接更新 Zone `CURRENT_STATE` 與 Zone 屬性，不建立 Observation
節點（避免長時間執行累積歷史資料）。
"""

from __future__ import annotations

from typing import Any
import random
import time

from src.app_helper import get_agent_config

from navigation_scenario.config import (
    ZONES,
    STATE_CROWDED,
    STATE_NORMAL,
    STATE_SPARSE,
)
from navigation_scenario.sensors.base import ScenarioSensorAgent, SensorUpdate


CROWD_LEVELS = [STATE_CROWDED, STATE_NORMAL, STATE_SPARSE]
FLOW_DIRECTIONS = ["north", "south", "east", "west", "mixed", "static"]


# 事件 kind → 強制狀態 / 對應密度的對照
_EVENT_TO_STATE: dict[str, tuple[str, float]] = {
    "crowd_congestion": (STATE_CROWDED, 0.90),
    "crowd_clear": (STATE_NORMAL, 0.40),
    "crowd_sparse": (STATE_SPARSE, 0.15),
}


_UPDATE_CYPHER = """
UNWIND $rows AS row
MATCH (z:Zone {name: row.zone})
OPTIONAL MATCH (z)-[old:CURRENT_STATE]->()
DELETE old
WITH DISTINCT z, row
MATCH (st:State {status_name: row.level})
CREATE (z)-[r:CURRENT_STATE {
    updated_at: datetime(),
    source: 'pedestrian_flow_sensor'
}]->(st)
SET z.density = row.density,
    z.flow_direction = row.flow_direction,
    z.visitor_count_est = row.visitor_count_est,
    z.last_sensor_update = datetime()
"""


class PedestrianFlowSensorAgent(ScenarioSensorAgent):
    """
    人流感測器：較高聯絡機率、較短輪詢間隔（人潮變化快）。

    每次聯絡 Blackboard 只更新「已存在」的 Zone 節點屬性與 CURRENT_STATE 關係，
    完全不建立 Observation 節點，因此長時間運行不會讓 Blackboard 膨脹。

    支援的注入事件 kind（透過 MQTT `inject_event`）：
        - `crowd_congestion`：強制指定 zone(s) 進入 Crowded
        - `crowd_clear`     ：強制指定 zone(s) 恢復 Normal
        - `crowd_sparse`    ：強制指定 zone(s) 為 Sparse
    target 可為單一 zone 字串，或字串列表。
    """

    HANDLED_EVENT_KINDS = ("crowd_congestion", "crowd_clear", "crowd_sparse")

    def __init__(
        self,
        name: str = "nav_pedestrian_flow_sensor",
        agent_config: dict[str, Any] | None = None,
        *,
        zones: list[str] | None = None,
        poll_interval_sec: float = 2.0,
        contact_probability: float = 0.55,
        rng: random.Random | None = None,
    ):
        agent_config = agent_config or get_agent_config()
        self._zones = zones or [z for z in ZONES if z not in ("Exit_Hall",)]
        self._last_crowd: dict[str, str] = {}
        self._last_density: dict[str, float] = {}

        # 可動態調整的隨機分布：[Crowded, Normal, Sparse]
        self._crowd_weights: list[float] = [0.25, 0.55, 0.20]
        self._resample_probability: float = 0.55

        super().__init__(
            name,
            agent_config,
            poll_interval_sec=poll_interval_sec,
            poll_jitter_sec=0.8,
            contact_probability=contact_probability,
            rng=rng,
        )

    def sensor_kind(self) -> str:
        return "pedestrian_flow"

    # ------------------------------------------------------------------
    # 隨機參數可調介面
    # ------------------------------------------------------------------
    def get_random_params(self) -> dict[str, Any]:
        return {
            "crowd_weights": list(self._crowd_weights),
            "resample_probability": self._resample_probability,
            "tracked_zones": list(self._zones),
        }

    def set_random_param(self, name: str, value: Any):
        if name == "crowd_weights":
            try:
                w = [max(0.0, float(x)) for x in value]
                if len(w) != 3 or sum(w) <= 0:
                    return (False, None, "crowd_weights must be [c,n,s] with sum>0")
                self._crowd_weights = w
                return (True, list(self._crowd_weights), None)
            except (TypeError, ValueError) as e:
                return (False, None, f"invalid crowd_weights: {e}")
        if name == "resample_probability":
            try:
                p = max(0.0, min(1.0, float(value)))
                self._resample_probability = p
                return (True, p, None)
            except (TypeError, ValueError) as e:
                return (False, None, f"invalid resample_probability: {e}")
        return super().set_random_param(name, value)

    # ------------------------------------------------------------------
    # 事件覆寫
    # ------------------------------------------------------------------
    def _collect_forced_zones(self) -> dict[str, tuple[str, float]]:
        """把 event override 攤平成 {zone: (level, density)} 的字典。"""
        forced: dict[str, tuple[str, float]] = {}
        for ov in self._pop_overrides():
            if ov.kind not in _EVENT_TO_STATE:
                continue
            level, default_density = _EVENT_TO_STATE[ov.kind]
            density = float(ov.payload.get("density", default_density))
            targets = ov.target
            if targets is None:
                targets = list(self._zones)
            elif isinstance(targets, str):
                targets = [targets]
            for z in targets:
                if z in self._zones:
                    forced[z] = (level, density)
        return forced

    def sample_update(self) -> SensorUpdate | None:
        forced_zones = self._collect_forced_zones()

        rows: list[dict[str, Any]] = []
        changed_zones: list[str] = []
        injected: list[str] = []

        for zone in self._zones:
            prev = self._last_crowd.get(zone)

            if zone in forced_zones:
                level, forced_density = forced_zones[zone]
                density = forced_density
                self._last_crowd[zone] = level
                self._last_density[zone] = density
                injected.append(zone)
                if level != prev:
                    changed_zones.append(zone)
            else:
                if prev is None or self._rng.random() < self._resample_probability:
                    level = self._rng.choices(
                        CROWD_LEVELS, weights=self._crowd_weights, k=1
                    )[0]
                    self._last_crowd[zone] = level
                    if level != prev:
                        changed_zones.append(zone)
                else:
                    level = prev

                density = self._last_density.get(zone, 0.4)
                if self._rng.random() < 0.5:
                    density = max(0.05, min(0.98, density + self._rng.uniform(-0.25, 0.25)))
                    self._last_density[zone] = density

            rows.append(
                {
                    "zone": zone,
                    "level": level,
                    "density": round(density, 2),
                    "flow_direction": self._rng.choice(FLOW_DIRECTIONS),
                    "visitor_count_est": int(density * 30),
                }
            )

        if not rows:
            return None

        crowded = [r["zone"] for r in rows if r["level"] == STATE_CROWDED]
        sparse = [r["zone"] for r in rows if r["level"] == STATE_SPARSE]

        injected_note = ""
        if injected:
            inj_text = "、".join(injected[:5])
            if len(injected) > 5:
                inj_text += f" 等 {len(injected)} 區"
            injected_note = f"（含事件注入：{inj_text}）"

        if changed_zones:
            highlight = "、".join(changed_zones[:5])
            if len(changed_zones) > 5:
                highlight += f" 等 {len(changed_zones)} 區"
            info_text = (
                f"人流感測：偵測到 {len(changed_zones)} 區人潮狀態變化（{highlight}）"
                f"{injected_note}；目前擁擠 {len(crowded)} 區、稀疏 {len(sparse)} 區，"
                f"已更新 {len(rows)} 個 Zone 狀態與密度屬性。"
            )
        else:
            info_text = (
                f"人流感測：掃描 {len(rows)} 區無顯著變化{injected_note}，"
                f"擁擠 {len(crowded)} 區、稀疏 {len(sparse)} 區，"
                f"更新 Zone 屬性。"
            )

        return SensorUpdate(
            kind="write",
            cypher=_UPDATE_CYPHER,
            params={"rows": rows, "ts": time.time()},
            summary=(
                f"crowd zones={len(crowded)} changed={len(changed_zones)} "
                f"injected={len(injected)}"
            ),
            info_text=info_text,
        )

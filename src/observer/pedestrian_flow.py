# src/observer/pedestrian_flow.py
"""
人流與動線狀態代理 (Pedestrian & Flow Observer)

專注於展場內「人」的動態：
- 監測哪些區域正處於擁擠狀態
- 人群的移動方向（動線）
- 機器人周遭是否有正在靠近或停留的訪客

黑板貢獻：
- 擁擠熱點圖（Zone 級 crowd_level）
- 周邊訪客密度（nearby_density）

透過 AgentFlow 的 blackboard.control 與黑板代理互動。
"""

from __future__ import annotations

import random
import time
from typing import Any, Optional

from src.app_helper import get_agent_config
from src.log_helper import init_logging

from .base import ObserverAgent
from .observation import (
    Observation,
    ObservationType,
    SaliencyLevel,
    Entity,
    Relation,
)

logger = init_logging()

# 展場 Zone 名稱（與 seed_blackboard 一致）
DEFAULT_ZONES = ["Main_Hall", "AI_Tech_Area", "Gaming_Area"]

# 人潮狀態對應 State.status_name
CROWD_STATES = ["Crowded", "Normal", "Sparse"]


class PedestrianFlowObserver(ObserverAgent):
    """
    人流與動線狀態代理

    持續監測 Zone 人潮、動線方向、周邊訪客密度，
    並將觀察結果提交至黑板代理。
    """

    def __init__(
        self,
        name: str = "pedestrian_flow_observer",
        agent_config: dict[str, Any] | None = None,
        *,
        poll_interval_sec: float = 2.0,
        zones: list[str] | None = None,
    ):
        agent_config = agent_config or get_agent_config()
        self._zones = zones or DEFAULT_ZONES
        self._last_crowd: dict[str, str] = {}
        self._last_density: dict[str, float] = {}

        super().__init__(
            name=name,
            agent_config=agent_config,
            poll_interval_sec=poll_interval_sec,
            min_saliency=SaliencyLevel.LOW,
            cooldown_sec=0.5,
        )

    def get_observation_type(self) -> ObservationType:
        return ObservationType.PEDESTRIAN_FLOW

    def get_urgency_keywords(self) -> list[str]:
        return ["擁擠", "堵塞", "crowded", "blocked", "緊急", "urgent"]

    def observe(self) -> Optional[Observation]:
        """
        執行人流觀察

        模擬：隨機變動各 Zone 人潮與周邊密度，
        若有顯著變化則產生 Observation。
        """
        entities: list[Entity] = []
        state_changed = False

        for zone in self._zones:
            # 模擬人潮狀態（50% 維持、50% 變動，利於導航情境中看見路線變更）
            crowd_level = self._last_crowd.get(zone)
            if crowd_level is None or random.random() < 0.5:
                crowd_level = random.choice(CROWD_STATES)
                self._last_crowd[zone] = crowd_level
                state_changed = True

            # 模擬密度 0.0 ~ 1.0
            density = self._last_density.get(zone, 0.5)
            if random.random() < 0.4:
                density = max(0.0, min(1.0, density + (random.random() - 0.5) * 0.3))
                self._last_density[zone] = density
                state_changed = True

            entity = Entity(
                entity_type="Zone",
                label=zone,
                entity_id=f"zone_{zone}",
                properties={
                    "crowd_level": crowd_level,
                    "density": round(density, 2),
                    "flow_direction": random.choice(["north", "south", "east", "west", "mixed"]),
                },
                confidence=0.9,
            )
            entities.append(entity)

        # 周邊訪客密度（以 Main_Hall 入口為例）
        nearby_density = self._last_density.get("Main_Hall", 0.5)
        entities.append(
            Entity(
                entity_type="NearbyDensity",
                label="entrance_nearby",
                entity_id="nearby_entrance",
                properties={
                    "location_id": "P_Entrance",
                    "visitor_count_est": int(nearby_density * 20),
                    "density": round(nearby_density, 2),
                },
                confidence=0.85,
            )
        )

        if not state_changed and not entities:
            return None

        saliency = SaliencyLevel.MODERATE
        if any(e.properties.get("crowd_level") == "Crowded" for e in entities if "crowd_level" in e.properties):
            saliency = SaliencyLevel.HIGH

        crowded_zones = [e.label for e in entities if e.properties.get("crowd_level") == "Crowded"]
        raw_desc = f"人潮觀察: {len(crowded_zones)} 區擁擠" if crowded_zones else "人潮觀察: 各區正常"

        return self.create_observation(
            entities=entities,
            raw_description=raw_desc,
            confidence=0.9,
            saliency=saliency,
            metadata={
                "crowd_hotspots": [z for z in self._zones if self._last_crowd.get(z) == "Crowded"],
                "observation_time": time.time(),
            },
        )

# src/observer/facility_event.py
"""
設施與活動狀態代理 (Facility & Event Observer)

專注於展場「靜態與定時」的資訊：
- 各展位狀態（是否開放）
- 目前的活動議程（講座是否開始）
- 展場設施（洗手間、休息區）的位置與狀態

黑板貢獻：
- 場景知識（Context）：將 KG 與當前時間軸結合
- 讓系統知道現在什麼地方「值得推薦」

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
)

logger = init_logging()

# 與 seed_blackboard 一致
DEFAULT_BOOTHS = [
    {"id": "B_A1", "exhibitor": "TechCorp AI"},
    {"id": "B_A2", "exhibitor": "Robotics Inc"},
    {"id": "B_B1", "exhibitor": "GameStudio X"},
]
DEFAULT_FACILITIES = [
    {"id": "P_Info", "name": "Information Desk", "type": "info"},
    {"id": "P_Restroom", "name": "Restroom_South", "type": "restroom"},
    {"id": "P_Entrance", "name": "Main Entrance", "type": "entrance"},
]
# 模擬活動議程
DEFAULT_EVENTS = [
    {"id": "EVT_001", "title": "AI 論壇", "booth_id": "B_A1", "start_min": 30, "duration_min": 60},
    {"id": "EVT_002", "title": "機器人展示", "booth_id": "B_A2", "start_min": 0, "duration_min": 120},
    {"id": "EVT_003", "title": "遊戲試玩", "booth_id": "B_B1", "start_min": 45, "duration_min": 90},
]


class FacilityEventObserver(ObserverAgent):
    """
    設施與活動狀態代理

    監控展位開放狀態、活動議程、設施狀態，
    提供場景知識供行動者代理參考。
    """

    def __init__(
        self,
        name: str = "facility_event_observer",
        agent_config: dict[str, Any] | None = None,
        *,
        poll_interval_sec: float = 3.0,
        booths: list[dict] | None = None,
        facilities: list[dict] | None = None,
        events: list[dict] | None = None,
    ):
        agent_config = agent_config or get_agent_config()
        self._booths = booths or DEFAULT_BOOTHS
        self._facilities = facilities or DEFAULT_FACILITIES
        self._events = events or DEFAULT_EVENTS
        self._session_start = time.time()
        self._last_booth_status: dict[str, str] = {}
        self._last_event_status: dict[str, str] = {}

        super().__init__(
            name=name,
            agent_config=agent_config,
            poll_interval_sec=poll_interval_sec,
            min_saliency=SaliencyLevel.LOW,
            cooldown_sec=1.0,
        )

    def get_observation_type(self) -> ObservationType:
        return ObservationType.FACILITY_EVENT

    def get_urgency_keywords(self) -> list[str]:
        return ["開放", "開始", "open", "started", "關閉", "closed"]

    def _elapsed_minutes(self) -> float:
        return (time.time() - self._session_start) / 60.0

    def observe(self) -> Optional[Observation]:
        """
        執行設施與活動觀察

        模擬：展位開放狀態、活動是否進行中、設施狀態。
        """
        entities: list[Entity] = []
        state_changed = False

        # 展位狀態（open / closed）
        for booth in self._booths:
            bid = booth["id"]
            status = self._last_booth_status.get(bid)
            if status is None or random.random() < 0.2:
                status = random.choice(["open", "open", "closed"])  # 2/3 機率開放
                self._last_booth_status[bid] = status
                state_changed = True

            entities.append(
                Entity(
                    entity_type="Booth",
                    label=booth["exhibitor"],
                    entity_id=bid,
                    properties={
                        "status": status,
                        "exhibitor": booth["exhibitor"],
                    },
                    confidence=1.0,
                )
            )

        # 設施狀態
        for fac in self._facilities:
            entities.append(
                Entity(
                    entity_type="Facility",
                    label=fac["name"],
                    entity_id=fac["id"],
                    properties={
                        "status": "available",
                        "facility_type": fac["type"],
                    },
                    confidence=1.0,
                )
            )

        # 活動議程（依模擬時間判斷是否進行中）
        elapsed = self._elapsed_minutes()
        active_events: list[str] = []

        for evt in self._events:
            start = evt["start_min"]
            end = start + evt["duration_min"]
            is_active = start <= elapsed < end
            evt_status = "in_progress" if is_active else ("upcoming" if elapsed < start else "ended")
            prev = self._last_event_status.get(evt["id"])
            if prev != evt_status:
                self._last_event_status[evt["id"]] = evt_status
                state_changed = True

            if is_active:
                active_events.append(evt["title"])

            entities.append(
                Entity(
                    entity_type="Event",
                    label=evt["title"],
                    entity_id=evt["id"],
                    properties={
                        "booth_id": evt["booth_id"],
                        "status": evt_status,
                        "start_min": evt["start_min"],
                        "duration_min": evt["duration_min"],
                    },
                    confidence=1.0,
                )
            )

        if not entities:
            return None

        saliency = SaliencyLevel.MODERATE
        if active_events:
            saliency = SaliencyLevel.HIGH

        raw_desc = f"設施觀察: {len(active_events)} 場活動進行中" if active_events else "設施觀察: 展位與設施狀態更新"

        return self.create_observation(
            entities=entities,
            raw_description=raw_desc,
            confidence=1.0,
            saliency=saliency,
            metadata={
                "active_events": active_events,
                "recommendable_booths": [b["id"] for b in self._booths if self._last_booth_status.get(b["id"]) == "open"],
                "observation_time": time.time(),
                "elapsed_minutes": round(elapsed, 1),
            },
        )

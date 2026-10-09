"""
數位／場館 API 感測器代理

模擬：場館管理系統公告（區域臨時關閉、人潮警示、改道、活動取消）。
以 `write` 直接更新 Zone `CURRENT_STATE`，不建立 Observation 節點。
"""

from __future__ import annotations

from typing import Any
import random
import time

from src.app_helper import get_agent_config

from navigation_scenario.config import (
    ZONES,
    STATE_CLOSED,
    STATE_CROWDED,
    STATE_NORMAL,
)
from navigation_scenario.sensors.base import ScenarioSensorAgent, SensorUpdate

API_EVENT_TYPES = [
    "zone_closure",
    "crowd_alert",
    "route_advisory",
    "event_cancellation",
]


# 注入事件 → (api_event_type, target_state)
_INJECT_KIND_MAP: dict[str, tuple[str, str]] = {
    "area_closure": ("zone_closure", STATE_CLOSED),
    "area_reopen": ("event_cancellation", STATE_NORMAL),
    "crowd_alert": ("crowd_alert", STATE_CROWDED),
    "route_advisory": ("route_advisory", STATE_NORMAL),
}

_EVENT_ZH = {
    "zone_closure": "區域臨時關閉",
    "crowd_alert": "人潮壅塞警示",
    "route_advisory": "改道建議（狀態恢復 Normal）",
    "event_cancellation": "活動取消，區域恢復開放",
}


_UPDATE_ZONE_STATE = """
MATCH (z:Zone {name: $zone})
OPTIONAL MATCH (z)-[old:CURRENT_STATE]->()
DELETE old
WITH DISTINCT z
MATCH (st:State {status_name: $state})
CREATE (z)-[r:CURRENT_STATE {
    updated_at: datetime(),
    source: 'digital_sensor',
    api_event: $event_type
}]->(st)
"""


class DigitalSensorAgent(ScenarioSensorAgent):
    """
    數位感測器：較長輪詢、中等聯絡機率（模擬 API 輪詢）。

    僅更新既有 Zone 的 `CURRENT_STATE`（DELETE 舊關係 → CREATE 新關係），
    不建立 Observation 節點。

    支援的注入事件 kind：
        - `area_closure`  ：將 Zone 強制設為 Closed（api_event=zone_closure）
        - `area_reopen`   ：將 Zone 恢復為 Normal
        - `crowd_alert`   ：將 Zone 設為 Crowded
        - `route_advisory`：將 Zone 設為 Normal 並標記改道建議
    target 為 Zone 名稱（字串或列表）。
    """

    HANDLED_EVENT_KINDS = (
        "area_closure",
        "area_reopen",
        "crowd_alert",
        "route_advisory",
    )

    def __init__(
        self,
        name: str = "nav_digital_sensor",
        agent_config: dict[str, Any] | None = None,
        *,
        poll_interval_sec: float = 4.0,
        contact_probability: float = 0.38,
        rng: random.Random | None = None,
    ):
        agent_config = agent_config or get_agent_config()
        self._zones = [z for z in ZONES if z not in ("Main_Hall", "Exit_Hall")]
        self._zone_state: dict[str, str] = {}
        # 可動態調整：API 事件權重對應 API_EVENT_TYPES 順序
        self._api_event_weights: list[float] = [0.35, 0.30, 0.25, 0.10]
        self._skip_unchanged_probability: float = 0.6

        super().__init__(
            name,
            agent_config,
            poll_interval_sec=poll_interval_sec,
            poll_jitter_sec=1.5,
            contact_probability=contact_probability,
            rng=rng,
        )

    def sensor_kind(self) -> str:
        return "digital"

    # ------------------------------------------------------------------
    # 隨機參數
    # ------------------------------------------------------------------
    def get_random_params(self) -> dict[str, Any]:
        return {
            "api_event_types": list(API_EVENT_TYPES),
            "api_event_weights": list(self._api_event_weights),
            "skip_unchanged_probability": self._skip_unchanged_probability,
        }

    def set_random_param(self, name: str, value: Any):
        if name == "api_event_weights":
            try:
                w = [max(0.0, float(x)) for x in value]
                if len(w) != len(API_EVENT_TYPES) or sum(w) <= 0:
                    return (
                        False,
                        None,
                        f"api_event_weights must be length {len(API_EVENT_TYPES)} and sum>0",
                    )
                self._api_event_weights = w
                return (True, list(self._api_event_weights), None)
            except (TypeError, ValueError) as e:
                return (False, None, f"invalid api_event_weights: {e}")
        if name == "skip_unchanged_probability":
            try:
                p = max(0.0, min(1.0, float(value)))
                self._skip_unchanged_probability = p
                return (True, p, None)
            except (TypeError, ValueError) as e:
                return (False, None, f"invalid skip_unchanged_probability: {e}")
        return super().set_random_param(name, value)

    # ------------------------------------------------------------------
    # 事件覆寫處理
    # ------------------------------------------------------------------
    def _build_inject_statement(
        self, kind: str, target: Any
    ) -> tuple[str, dict[str, Any], str] | None:
        if kind not in _INJECT_KIND_MAP:
            return None
        event_type, target_state = _INJECT_KIND_MAP[kind]
        if isinstance(target, str):
            zone = target
        elif isinstance(target, (list, tuple)) and target:
            zone = str(target[0])
        else:
            return None
        if zone not in self._zones:
            # 注入未知 zone：仍嘗試寫，讓 MATCH 自然 no-op
            pass
        self._zone_state[zone] = target_state
        params = {
            "zone": zone,
            "state": target_state,
            "event_type": event_type,
            "ts": time.time(),
        }
        info = (
            f"場館 API（事件注入）：「{_EVENT_ZH.get(event_type, event_type)}」，"
            f"將 Zone「{zone}」狀態更新為 {target_state}。"
        )
        return (_UPDATE_ZONE_STATE, params, info)

    def sample_update(self) -> SensorUpdate | None:
        # 1) 處理事件注入 ─ 可一次處理多個 zone
        overrides = self._pop_overrides()
        if overrides:
            statements: list[tuple[str, dict[str, Any]]] = []
            descs: list[str] = []
            for ov in overrides:
                targets = ov.target
                if isinstance(targets, (list, tuple)):
                    iter_targets: list[Any] = list(targets)
                else:
                    iter_targets = [targets]
                for t in iter_targets:
                    built = self._build_inject_statement(ov.kind, t)
                    if built is None:
                        continue
                    cy, ps, info = built
                    statements.append((cy, ps))
                    descs.append(info)
            if statements:
                return SensorUpdate(
                    kind="write",
                    statements=statements,
                    summary=f"event_inject digital count={len(statements)}",
                    info_text="；".join(descs),
                )

        # 2) 隨機路徑（維持原行為）
        if not self._zones:
            return None

        event_type = self._rng.choices(
            API_EVENT_TYPES,
            weights=self._api_event_weights,
            k=1,
        )[0]
        zone = self._rng.choice(self._zones)

        if event_type == "zone_closure":
            target_state = STATE_CLOSED
        elif event_type == "crowd_alert":
            target_state = STATE_CROWDED
        else:
            target_state = STATE_NORMAL

        if (
            self._zone_state.get(zone) == target_state
            and self._rng.random() < self._skip_unchanged_probability
        ):
            return None
        self._zone_state[zone] = target_state

        info_text = (
            f"場館 API：收到「{_EVENT_ZH.get(event_type, event_type)}」通告，"
            f"將 Zone「{zone}」狀態更新為 {target_state}。"
        )

        return SensorUpdate(
            kind="write",
            cypher=_UPDATE_ZONE_STATE,
            params={
                "zone": zone,
                "state": target_state,
                "event_type": event_type,
                "ts": time.time(),
            },
            summary=f"{event_type} zone={zone} state={target_state}",
            info_text=info_text,
        )

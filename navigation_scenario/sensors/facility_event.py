"""
設施／展位感測器代理

模擬：展位開放狀態、設施可用性。
以 `write` 命令直接更新既有的 Booth / POI 屬性，不建立 Observation 節點。
"""

from __future__ import annotations

from typing import Any
import random
import time

from src.app_helper import get_agent_config

from navigation_scenario.config import BOOTHS, POIS
from navigation_scenario.sensors.base import ScenarioSensorAgent, SensorUpdate


_UPDATE_BOOTHS = """
UNWIND $rows AS row
MATCH (b:Booth {id: row.id})
SET b.status = row.status,
    b.exhibitor = row.exhibitor,
    b.last_sensor_update = datetime(),
    b.update_source = 'facility_event_sensor'
"""


_UPDATE_FACILITIES = """
UNWIND $rows AS row
MATCH (p) WHERE p.id = row.id AND (p:POI OR p:Facility)
SET p.facility_status = row.status,
    p.facility_type = row.facility_type,
    p.last_sensor_update = datetime(),
    p.update_source = 'facility_event_sensor'
"""


class FacilityEventSensorAgent(ScenarioSensorAgent):
    """
    設施感測器：中等聯絡機率、較長輪詢（展位／活動變化較慢）。

    支援的注入事件 kind：
        - `booth_closure`：強制指定展位關閉（target=展位 id 或 id 列表）
        - `booth_reopen` ：強制指定展位重新開放
        - `facility_outage`：強制指定設施 (POI) 不可用
        - `facility_restore`：強制指定設施恢復可用
    """

    HANDLED_EVENT_KINDS = (
        "booth_closure",
        "booth_reopen",
        "facility_outage",
        "facility_restore",
    )

    def __init__(
        self,
        name: str = "nav_facility_event_sensor",
        agent_config: dict[str, Any] | None = None,
        *,
        poll_interval_sec: float = 3.0,
        contact_probability: float = 0.40,
        rng: random.Random | None = None,
    ):
        agent_config = agent_config or get_agent_config()
        self._booths = list(BOOTHS)
        self._facilities = [
            {"id": p["id"], "name": p["name"], "type": "poi"}
            for p in POIS
            if p["id"] in ("P_Info", "P_Restroom_N", "P_Restroom_S", "P_Cafe", "P_Exit")
        ]
        self._last_booth_status: dict[str, str] = {}
        # 可動態調整的權重（open/open/closed → keep_open / new_open / closed）
        self._booth_status_weights: list[float] = [0.45, 0.35, 0.20]
        self._booth_resample_probability: float = 0.22
        self._facility_outage_probability: float = 0.08

        super().__init__(
            name,
            agent_config,
            poll_interval_sec=poll_interval_sec,
            poll_jitter_sec=1.0,
            contact_probability=contact_probability,
            rng=rng,
        )

    def sensor_kind(self) -> str:
        return "facility_event"

    # ------------------------------------------------------------------
    # 隨機參數
    # ------------------------------------------------------------------
    def get_random_params(self) -> dict[str, Any]:
        return {
            "booth_status_weights": list(self._booth_status_weights),
            "booth_resample_probability": self._booth_resample_probability,
            "facility_outage_probability": self._facility_outage_probability,
        }

    def set_random_param(self, name: str, value: Any):
        if name == "booth_status_weights":
            try:
                w = [max(0.0, float(x)) for x in value]
                if len(w) != 3 or sum(w) <= 0:
                    return (False, None, "booth_status_weights must be length 3 and sum>0")
                self._booth_status_weights = w
                return (True, list(self._booth_status_weights), None)
            except (TypeError, ValueError) as e:
                return (False, None, f"invalid booth_status_weights: {e}")
        if name in ("booth_resample_probability", "facility_outage_probability"):
            try:
                p = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError) as e:
                return (False, None, f"invalid {name}: {e}")
            if name == "booth_resample_probability":
                self._booth_resample_probability = p
            else:
                self._facility_outage_probability = p
            return (True, p, None)
        return super().set_random_param(name, value)

    # ------------------------------------------------------------------
    # 事件覆寫
    # ------------------------------------------------------------------
    def _collect_forced(
        self,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """回傳 (forced_booths, forced_facilities)，值為 status 字串。"""
        forced_booths: dict[str, str] = {}
        forced_facilities: dict[str, str] = {}
        for ov in self._pop_overrides():
            targets = ov.target
            if isinstance(targets, str):
                targets = [targets]
            elif targets is None:
                targets = []
            for tid in targets:
                if ov.kind == "booth_closure":
                    forced_booths[tid] = "closed"
                elif ov.kind == "booth_reopen":
                    forced_booths[tid] = "open"
                elif ov.kind == "facility_outage":
                    forced_facilities[tid] = "unavailable"
                elif ov.kind == "facility_restore":
                    forced_facilities[tid] = "available"
        return forced_booths, forced_facilities

    def sample_update(self) -> SensorUpdate | None:
        forced_booths, forced_facilities = self._collect_forced()
        injected_booths: list[str] = []
        injected_facilities: list[str] = []

        booth_rows: list[dict[str, Any]] = []
        closed_booths: list[str] = []

        for booth in self._booths:
            bid = booth["id"]
            prev = self._last_booth_status.get(bid)

            if bid in forced_booths:
                status = forced_booths[bid]
                self._last_booth_status[bid] = status
                injected_booths.append(bid)
            elif prev is None or self._rng.random() < self._booth_resample_probability:
                status = self._rng.choices(
                    ["open", "open", "closed"],
                    weights=self._booth_status_weights,
                    k=1,
                )[0]
                self._last_booth_status[bid] = status
            else:
                status = prev

            if status == "closed":
                closed_booths.append(bid)

            booth_rows.append(
                {
                    "id": bid,
                    "exhibitor": booth["exhibitor"],
                    "status": status,
                }
            )

        facility_rows: list[dict[str, Any]] = []
        for fac in self._facilities:
            fid = fac["id"]
            if fid in forced_facilities:
                status = forced_facilities[fid]
                injected_facilities.append(fid)
            else:
                avail = self._rng.random() > self._facility_outage_probability
                status = "available" if avail else "unavailable"
            facility_rows.append(
                {
                    "id": fid,
                    "facility_type": fac["type"],
                    "status": status,
                }
            )

        if not booth_rows and not facility_rows:
            return None

        statements: list[tuple[str, dict[str, Any]]] = []
        if booth_rows:
            statements.append((_UPDATE_BOOTHS, {"rows": booth_rows, "ts": time.time()}))
        if facility_rows:
            statements.append((_UPDATE_FACILITIES, {"rows": facility_rows, "ts": time.time()}))

        open_booths = len(booth_rows) - len(closed_booths)
        if closed_booths:
            highlight = "、".join(closed_booths[:5])
            if len(closed_booths) > 5:
                highlight += f" 等 {len(closed_booths)} 個"
            booth_desc = (
                f"展位 {open_booths} 開放／{len(closed_booths)} 關閉（{highlight}）"
            )
        else:
            booth_desc = f"{len(booth_rows)} 個展位全部開放"

        inject_note = ""
        if injected_booths or injected_facilities:
            parts = []
            if injected_booths:
                parts.append(f"展位 {','.join(injected_booths[:5])}")
            if injected_facilities:
                parts.append(f"設施 {','.join(injected_facilities[:5])}")
            inject_note = f"（事件注入：{'；'.join(parts)}）"

        info_text = (
            f"設施感測：{booth_desc}{inject_note}；"
            f"同時更新 {len(facility_rows)} 個服務設施狀態。"
        )

        return SensorUpdate(
            kind="write",
            statements=statements,
            summary=(
                f"booths closed={len(closed_booths)} facilities={len(facility_rows)} "
                f"injected={len(injected_booths) + len(injected_facilities)}"
            ),
            info_text=info_text,
        )

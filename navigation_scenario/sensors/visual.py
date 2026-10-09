"""
視覺感測器代理

模擬：通道障礙、臨時圍欄、可通行性變化。
以 `write` 命令直接更新既有的 `CONNECTED_TO` 邊上的 `blocked` 屬性，
偵測到障礙狀態變化時才聯絡 Blackboard，不建立 Observation 節點。
"""

from __future__ import annotations

from typing import Any
import random
import time

from src.app_helper import get_agent_config

from navigation_scenario.config import EDGES
from navigation_scenario.sensors.base import ScenarioSensorAgent, SensorUpdate


PROTECTED_NODES = frozenset({"P_Entrance", "P_Exit", "P_Info"})


_UPDATE_EDGE = """
MATCH (a) WHERE a.id = $a_id
MATCH (b) WHERE b.id = $b_id
MATCH (a)-[r:CONNECTED_TO]->(b)
SET r.blocked = $blocked,
    r.obstacle_type = $obstacle,
    r.last_sensor_update = datetime(),
    r.update_source = 'visual_sensor'
WITH a, b
MATCH (b)-[r2:CONNECTED_TO]->(a)
SET r2.blocked = $blocked,
    r2.obstacle_type = $obstacle,
    r2.last_sensor_update = datetime(),
    r2.update_source = 'visual_sensor'
"""


_OBSTACLE_ZH = {
    "barrier": "圍欄",
    "crowd_barrier": "人潮阻擋",
    "construction": "施工",
    "equipment": "設備佔用",
    "clear": "暢通",
}


def _parse_edge_target(target: Any) -> tuple[str | None, str | None]:
    """支援 'A->B' / 'A,B' / ['A','B'] / {'from':A,'to':B} 等格式。"""
    if target is None:
        return (None, None)
    if isinstance(target, dict):
        a = target.get("from") or target.get("a") or target.get("a_id")
        b = target.get("to") or target.get("b") or target.get("b_id")
        return (str(a) if a else None, str(b) if b else None)
    if isinstance(target, (list, tuple)) and len(target) >= 2:
        return (str(target[0]), str(target[1]))
    if isinstance(target, str):
        for sep in ("->", "=>", "|", ","):
            if sep in target:
                a, b = target.split(sep, 1)
                return (a.strip(), b.strip())
    return (None, None)


class VisualSensorAgent(ScenarioSensorAgent):
    """
    視覺感測器：較低聯絡機率（僅在偵測到障礙狀態變化時回報）。

    僅更新既有 `CONNECTED_TO` 邊的屬性（idempotent SET），不建立任何
    `Observation` / `PathSegment` 等歷史節點。

    支援的注入事件 kind：
        - `route_detour`：強制指定邊（雙向）封鎖。
                         target: {"from": "B_RB1", "to": "B_RB2"} 或 "B_RB1->B_RB2"
                         payload.obstacle_type 可指定障礙類型（預設 'barrier'）
        - `route_clear` ：強制清除指定邊封鎖。
    """

    HANDLED_EVENT_KINDS = ("route_detour", "route_clear")

    def __init__(
        self,
        name: str = "nav_visual_sensor",
        agent_config: dict[str, Any] | None = None,
        *,
        poll_interval_sec: float = 1.8,
        contact_probability: float = 0.28,
        block_probability: float = 0.65,
        clear_probability: float = 0.35,
        rng: random.Random | None = None,
    ):
        agent_config = agent_config or get_agent_config()
        self._block_probability = max(0.0, min(1.0, block_probability))
        self._clear_probability = max(0.0, min(1.0, clear_probability))
        self._blocked_edges: set[tuple[str, str]] = set()
        self._edge_pool = [
            (a, b)
            for a, b, _ in EDGES
            if a not in PROTECTED_NODES and b not in PROTECTED_NODES
        ]

        super().__init__(
            name,
            agent_config,
            poll_interval_sec=poll_interval_sec,
            poll_jitter_sec=0.6,
            contact_probability=contact_probability,
            rng=rng,
        )

    def sensor_kind(self) -> str:
        return "visual"

    # ------------------------------------------------------------------
    # 隨機參數
    # ------------------------------------------------------------------
    def get_random_params(self) -> dict[str, Any]:
        return {
            "block_probability": self._block_probability,
            "clear_probability": self._clear_probability,
            "blocked_edges": [f"{a}->{b}" for a, b in self._blocked_edges],
        }

    def set_random_param(self, name: str, value: Any):
        if name in ("block_probability", "clear_probability"):
            try:
                p = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError) as e:
                return (False, None, f"invalid {name}: {e}")
            if name == "block_probability":
                self._block_probability = p
            else:
                self._clear_probability = p
            return (True, p, None)
        return super().set_random_param(name, value)

    # ------------------------------------------------------------------
    # 事件覆寫處理
    # ------------------------------------------------------------------
    def _build_override_statement(
        self, kind: str, target: Any, payload: dict[str, Any]
    ) -> tuple[str, dict[str, Any], str] | None:
        a_id, b_id = _parse_edge_target(target)
        if not a_id or not b_id:
            return None
        blocked = kind == "route_detour"
        if blocked:
            obstacle = str(payload.get("obstacle_type", "barrier"))
            self._blocked_edges.add((a_id, b_id))
        else:
            obstacle = "clear"
            self._blocked_edges.discard((a_id, b_id))
        params = {
            "a_id": a_id,
            "b_id": b_id,
            "blocked": blocked,
            "obstacle": obstacle,
            "ts": time.time(),
        }
        verb = "封鎖" if blocked else "清除"
        obstacle_zh = _OBSTACLE_ZH.get(obstacle, obstacle)
        info = (
            f"視覺感測（事件注入）：{verb}通道 {a_id} → {b_id}"
            + (f"，障礙類型：{obstacle_zh}" if blocked else "")
        )
        return (_UPDATE_EDGE, params, info)

    def sample_update(self) -> SensorUpdate | None:
        # 1) 若有事件覆寫，依注入內容產出 statements
        overrides = self._pop_overrides()
        if overrides:
            statements: list[tuple[str, dict[str, Any]]] = []
            descriptions: list[str] = []
            for ov in overrides:
                built = self._build_override_statement(ov.kind, ov.target, ov.payload)
                if built is None:
                    continue
                cy, ps, info = built
                statements.append((cy, ps))
                descriptions.append(info)
            if statements:
                return SensorUpdate(
                    kind="write",
                    statements=statements,
                    summary=f"event_inject visual count={len(statements)}",
                    info_text="；".join(descriptions),
                )

        # 2) 沒有事件 → 隨機抽樣（維持原行為）
        if not self._edge_pool:
            return None

        a_id, b_id = self._rng.choice(self._edge_pool)
        key = (a_id, b_id)

        if key in self._blocked_edges:
            if self._rng.random() < self._clear_probability:
                self._blocked_edges.discard(key)
                blocked = False
            else:
                return None
        else:
            if self._rng.random() < self._block_probability:
                self._blocked_edges.add(key)
                blocked = True
            else:
                return None

        obstacle_types = ["barrier", "crowd_barrier", "construction", "equipment"]
        obstacle = "clear" if not blocked else self._rng.choice(obstacle_types)
        obstacle_zh = _OBSTACLE_ZH.get(obstacle, obstacle)

        if blocked:
            info_text = (
                f"視覺感測：偵測到通道 {a_id} → {b_id} 出現{obstacle_zh}，"
                f"將該邊（雙向）標記為封鎖。"
            )
        else:
            info_text = (
                f"視覺感測：原封鎖通道 {a_id} → {b_id} 障礙已清除，"
                f"恢復為可通行（雙向解鎖）。"
            )

        return SensorUpdate(
            kind="write",
            cypher=_UPDATE_EDGE,
            params={
                "a_id": a_id,
                "b_id": b_id,
                "blocked": blocked,
                "obstacle": obstacle,
                "ts": time.time(),
            },
            summary=f"edge {a_id}->{b_id} blocked={blocked} ({obstacle})",
            info_text=info_text,
        )

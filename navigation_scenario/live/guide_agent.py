"""
GuideAgent：live 模式的嚮導機器人（navigation executor）。

與 NavigationAgent 訂閱相同的 navigation.request / navigation.cancel、回報 navigation.result，
但導航類 task 會在 Blackboard 圖上實際移動：

- 每走一條邊前重新讀取圖快照、依當下狀態重算最短路徑；遇到封鎖通道或封閉區會就地繞行
- 封鎖的通道與狀態為 Closed 的區域一律不可通行（目的地本身除外）；
  avoid_crowded 依任務參數設定，並在同一個案例（session）內延續到後續任務
- 走不到目的地時拋出 RouteUnavailable（navigation.result 的 ok=false），
  交由 IntentionalAgent 決定 retry / replan
- 機器人位置跨任務保留：重規劃後的新任務從目前位置出發，不採用 LLM 給的 current_location
- 同一時間只走一個導航任務（平行派工的導航任務會排隊）
- 每一步記錄在 trajectory，供 benchmark 計算實際距離、到達的目標與限制違反
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from src.agents._executor_utils import CancelToken
from src.agents.navigation_agent import NavigationAgent
from src.log_helper import init_logging

from navigation_scenario.config import (
    BOOTHS,
    FACILITY_POIS,
    POI_ALIASES,
    POIS,
    STATE_CLOSED,
    STATE_CROWDED,
    ZONE_LABELS,
)
from navigation_scenario.pathing import (
    GraphSnapshot,
    PathResult,
    resolve_target_node,
    shortest_path,
)

logger = init_logging()

# 會讓機器人移動的 task
MOVE_TASKS = frozenset({
    "LocateExhibit",
    "NavigationAssistance",
    "SuggestRoute",
    "ReplanRoute",
    "LocateFacility",
})

_HARD_CONSTRAINTS = ("avoid_closed",)   # 封閉區實際上無法通行
_SOFT_CONSTRAINTS = ("avoid_crowded",)  # 走不到時可放寬（會記錄在 trajectory）


class RouteUnavailable(RuntimeError):
    """目前狀態下沒有可通行的路線。"""


class UnknownDestination(ValueError):
    """無法把任務參數解析成圖上的節點。"""


@dataclass
class WalkStep:
    """機器人走過的一條邊。"""

    task: str
    task_id: str | None
    from_node: str
    to_node: str
    distance: float
    zone: str | None            # to_node 所在的 zone
    zone_state: str             # 走這一步時 to_node 所在 zone 的狀態
    at: float                   # time.monotonic()，用來與事件注入時間比較
    rerouted: bool = False      # 這一步是否因環境變動偏離先前規劃的路線
    relaxed: bool = False       # 是否放寬了 avoid_crowded 才找到路

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# =============================================================================
# 目的地解析
# =============================================================================
def _norm(text: str) -> str:
    return "".join(ch for ch in str(text).lower() if ch not in " _-　")


def _nearest(snap: GraphSnapshot, start: str | None, candidates: list[str]) -> str:
    candidates = [c for c in candidates if c in snap.nodes]
    if not candidates:
        raise UnknownDestination("no candidate node on the map")
    if start is None or start not in snap.nodes:
        return candidates[0]
    best, best_d = candidates[0], float("inf")
    for c in candidates:
        r = shortest_path(snap, start, c, constraints=_HARD_CONSTRAINTS)
        if r.reachable and r.distance < best_d:
            best, best_d = c, r.distance
    return best


def resolve_destination(snap: GraphSnapshot, raw: Any, *, start: str | None = None) -> str:
    """把 LLM 給的目的地（節點 id、zone、中文名稱、攤位名、facility:<type>）解析成圖上的節點。"""
    if raw is None or not str(raw).strip():
        raise UnknownDestination("missing destination")
    name = str(raw).strip().strip("\"'")

    if name.startswith("facility:"):
        kind = name.split(":", 1)[1]
        if kind not in FACILITY_POIS:
            raise UnknownDestination(f"unknown facility type: {kind}")
        return _nearest(snap, start, FACILITY_POIS[kind])
    if name in snap.nodes:
        # Zone 節點沒有通道，改走該區最近的 POI / 攤位
        if snap.nodes[name].get("label") == "Zone":
            return resolve_target_node(snap, name, "zone", start=start)
        return name

    key = _norm(name)
    zones = sorted({m.get("zone") for m in snap.nodes.values() if m.get("zone")})
    for zone in zones:
        if key in (_norm(zone), _norm(ZONE_LABELS.get(zone, ""))):
            return resolve_target_node(snap, zone, "zone", start=start)
    for booth in BOOTHS:
        if key == _norm(booth["exhibitor"]):
            return booth["id"]
    for poi in POIS:
        if key == _norm(poi["name"]):
            return poi["id"]
    for alias in sorted(POI_ALIASES, key=len, reverse=True):
        if alias in name:
            return POI_ALIASES[alias]
    if any(word in name for word in ("洗手間", "廁所")) or "restroom" in key:
        return _nearest(snap, start, FACILITY_POIS["restroom"])
    for zone in zones:
        label = ZONE_LABELS.get(zone, "")
        if (label and _norm(label) in key) or _norm(zone) in key:
            return resolve_target_node(snap, zone, "zone", start=start)
    raise UnknownDestination(f"cannot resolve destination: {raw!r}")


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    text = str(value).strip().strip("[]")
    return [p.strip().strip("\"'") for p in text.replace("，", ",").split(",") if p.strip().strip("\"'")]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def destinations_for(task: str, params: dict[str, Any]) -> list[str]:
    """依 task 從參數取出要依序前往的目的地（尚未解析成節點）。"""
    if task == "LocateExhibit":
        dests = [params.get("target_name") or params.get("destination")]
    elif task == "NavigationAssistance":
        dests = [params.get("destination") or params.get("target_name")]
    elif task == "SuggestRoute":
        dests = _as_list(params.get("waypoints")) + [params.get("destination") or params.get("target_name")]
    elif task == "ReplanRoute":
        dests = _as_list(params.get("remaining_goals")) or [params.get("destination")]
    elif task == "LocateFacility":
        kind = params.get("facility_type")
        dests = [f"facility:{kind}" if kind in FACILITY_POIS else (kind or params.get("destination"))]
    else:
        dests = []
    dests = [d for d in dests if d is not None and str(d).strip()]
    if not dests:
        raise UnknownDestination(f"{task} has no destination parameter: {params}")
    return dests


# =============================================================================
# GuideAgent
# =============================================================================
class GuideAgent(NavigationAgent):
    """在 Blackboard 圖上實際移動的嚮導機器人。"""

    def __init__(
        self,
        agent_config: dict[str, Any],
        *,
        snapshot_loader: Callable[[], GraphSnapshot],
        start: str = "P_Entrance",
        seconds_per_meter: float = 0.1,
        position_writer: Callable[[str], None] | None = None,
    ):
        super().__init__(agent_config)
        self.snapshot_loader = snapshot_loader
        self.seconds_per_meter = float(seconds_per_meter)
        self.position_writer = position_writer
        # 每走完一條邊、決定下一條邊之前呼叫（benchmark 在此注入事件，時機才可重現）
        self.step_listener: Callable[[WalkStep], None] | None = None
        self._move_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.position = start
        self.trajectory: list[WalkStep] = []
        self.preferences: set[str] = set()
        self.walked_distance = 0.0
        self.reset(start)

    # ------------------------------------------------------------------
    # Session 狀態（benchmark 每個案例開始前呼叫 reset）
    # ------------------------------------------------------------------
    def reset(self, start: str) -> None:
        with self._state_lock:
            self.position = start
            self.trajectory = []
            self.preferences = set()
            self.walked_distance = 0.0
        self._write_position(start)

    def snapshot_state(self) -> tuple[str, float, list[WalkStep]]:
        """回傳 (目前位置, 已走距離, trajectory 副本)，供 benchmark 在其他 thread 讀取。"""
        with self._state_lock:
            return self.position, self.walked_distance, list(self.trajectory)

    # ------------------------------------------------------------------
    # 任務執行
    # ------------------------------------------------------------------
    def execute_task(
        self,
        task: str,
        params: dict[str, Any],
        *,
        cancel_token: CancelToken | None = None,
        task_id: str | None = None,
    ) -> tuple[str, bool]:
        if task not in MOVE_TASKS:
            return super().execute_task(task, params, cancel_token=cancel_token, task_id=task_id)

        if _truthy(params.get("avoid_crowded", False)):
            with self._state_lock:
                self.preferences.add("avoid_crowded")
        dests = destinations_for(task, params)

        if not self._acquire_move_lock(cancel_token):
            return (f"{task} 已取消（等待前一個導航任務時）", True)
        try:
            reached: list[str] = []
            for raw in dests:
                target = resolve_destination(self.snapshot_loader(), raw, start=self.position)
                if not self._walk_to(target, task=task, task_id=task_id, cancel_token=cancel_token):
                    return (f"{task} 已取消，停在 {self.position}", True)
                reached.append(target)
            return (f"已抵達 {' → '.join(reached)}（目前位置 {self.position}）", False)
        finally:
            self._move_lock.release()

    def _acquire_move_lock(self, cancel_token: CancelToken | None) -> bool:
        while not self._move_lock.acquire(timeout=0.05):
            if cancel_token is not None and cancel_token.cancelled:
                return False
        return True

    def _route(self, snap: GraphSnapshot, start: str, target: str) -> tuple[PathResult, bool]:
        with self._state_lock:
            soft = [c for c in _SOFT_CONSTRAINTS if c in self.preferences]
        path = shortest_path(snap, start, target, constraints=(*_HARD_CONSTRAINTS, *soft))
        if path.reachable or not soft:
            return path, False
        relaxed = shortest_path(snap, start, target, constraints=_HARD_CONSTRAINTS)
        return relaxed, relaxed.reachable

    def _walk_to(self, target: str, *, task: str, task_id: str | None, cancel_token: CancelToken | None) -> bool:
        """沿最短路徑逐邊走到 target；被取消時回傳 False（停在最後到達的節點）。"""
        planned_rest: list[str] = []
        while self.position != target:
            if cancel_token is not None and cancel_token.cancelled:
                return False
            snap = self.snapshot_loader()
            path, relaxed = self._route(snap, self.position, target)
            if not path.reachable:
                raise RouteUnavailable(f"no passable route from {self.position} to {target}")
            nxt = path.nodes[1]
            rerouted = bool(planned_rest) and path.nodes[1:] != planned_rest
            distance = float(snap.edges[(self.position, nxt)].get("distance", 10))
            if not self._sleep(distance * self.seconds_per_meter, cancel_token):
                return False   # 在邊上被取消：視為沒走完，停在原節點
            zone = (snap.nodes.get(nxt) or {}).get("zone")
            step = WalkStep(
                task=task, task_id=task_id, from_node=self.position, to_node=nxt, distance=distance,
                zone=zone, zone_state=snap.zone_states.get(zone, "Normal") if zone else "Normal",
                at=time.monotonic(), rerouted=rerouted, relaxed=relaxed,
            )
            with self._state_lock:
                self.trajectory.append(step)
                self.walked_distance += distance
                self.position = nxt
            if rerouted:
                logger.info("GuideAgent rerouted at %s toward %s: %s", step.from_node, target, path.nodes)
            self._write_position(nxt)
            self._notify(step)
            planned_rest = path.nodes[2:]
        return True

    def _notify(self, step: WalkStep) -> None:
        listener = self.step_listener
        if listener is None:
            return
        try:
            listener(step)
        except Exception as e:
            logger.warning("GuideAgent step listener failed: %s", e)

    @staticmethod
    def _sleep(seconds: float, cancel_token: CancelToken | None) -> bool:
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            if cancel_token is not None and cancel_token.cancelled:
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(0.02, remaining))

    def _write_position(self, node: str) -> None:
        if self.position_writer is None:
            return
        try:
            self.position_writer(node)
        except Exception as e:
            logger.debug("GuideAgent position write failed: %s", e)


def avoid_states_for(constraints: tuple[str, ...] | list[str]) -> set[str]:
    """把案例限制轉成「不應經過」的 zone 狀態（evaluation 判斷限制違反用）。"""
    states: set[str] = set()
    if "avoid_crowded" in constraints:
        states.add(STATE_CROWDED)
    if "avoid_closed" in constraints:
        states.add(STATE_CLOSED)
    return states

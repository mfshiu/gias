"""
Experiment 1：單一 test case 執行核心

流程（一次 case）：
  1) reset Blackboard 為 baseline
  2) 依 ratio 注入「初始事件」(可能 0~N 個 zone/edge)
  3) 用 IntentionalAgent.plan_intention() 取得 plan
  4) 從 plan 萃取 atomic actions；判斷 plan_executable
  5) 模擬導航：用 Dijkstra 在當下 blackboard 子圖上找路；
     若無路徑且 constraints 啟用，視為 fail
  6) 在執行中段(到一半時)再依 ratio 注入「中途事件」；若使路徑被封鎖
     → 觸發 replan：再做一次 Dijkstra；若仍可達 → replan_success
  7) 累積 metrics 並回傳 CaseResult
"""

from __future__ import annotations

import heapq
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Optional

from src.kg.adapter_neo4j import Neo4jBoltAdapter
from src.core.intentional_agent import IntentionalAgent
from experiment1.config import (
    EXPERIMENT_PROFILE,
    NAV_ACTIONS,
    REPLAN_OVERHEAD_SEC,
    STATE_CLOSED,
    STATE_CROWDED,
    WALK_SPEED_MPS,
    POIS,
    BOOTHS,
)
from experiment1.event_injector import EventInjector
from experiment1.metrics import CaseResult
from experiment1.test_cases import TestCase
from experiment1.seed_blackboard import reset_to_baseline


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Blackboard graph helpers
# ---------------------------------------------------------------------------

@dataclass
class GraphSnapshot:
    nodes: dict[str, str]  # node_id -> zone_name
    edges: dict[tuple[str, str], dict]  # (a,b) -> {distance, blocked}
    zone_states: dict[str, str]  # zone -> state name


def _snapshot_graph(kg: Neo4jBoltAdapter) -> GraphSnapshot:
    nodes = {}
    for row in kg.query(
        """
        MATCH (n)-[:LOCATED_IN]->(z:Zone)
        WHERE n:POI OR n:Booth
        RETURN n.id AS id, z.name AS zone
        """,
        {},
    ):
        if row.get("id"):
            nodes[row["id"]] = row["zone"]

    edges: dict[tuple[str, str], dict] = {}
    for row in kg.query(
        """
        MATCH (a)-[r:CONNECTED_TO]->(b)
        WHERE a.id IS NOT NULL AND b.id IS NOT NULL
        RETURN a.id AS a, b.id AS b, r.distance AS d, coalesce(r.blocked, false) AS blocked
        """,
        {},
    ):
        edges[(row["a"], row["b"])] = {"distance": float(row["d"] or 0.0), "blocked": bool(row["blocked"])}

    zone_states: dict[str, str] = {}
    for row in kg.query(
        """
        MATCH (z:Zone)-[:CURRENT_STATE]->(st:State)
        RETURN z.name AS zone, st.status_name AS state
        """,
        {},
    ):
        zone_states[row["zone"]] = row["state"]

    return GraphSnapshot(nodes=nodes, edges=edges, zone_states=zone_states)


def _is_zone_blocked(state: str, *, avoid_crowded: bool, avoid_closed: bool) -> bool:
    if state == STATE_CLOSED and avoid_closed:
        return True
    if state == STATE_CROWDED and avoid_crowded:
        return True
    # Closed 一律不可進入（即使沒設 avoid_closed，封閉區依然不可達）
    if state == STATE_CLOSED:
        return True
    return False


def _zone_anchor(snap: GraphSnapshot, zone: str) -> Optional[str]:
    """選一個 zone 內的代表節點（POI 優先，否則 Booth）作為終點 anchor。"""
    poi_ids = {p["id"] for p in POIS if p["zone"] == zone}
    booth_ids = {b["id"] for b in BOOTHS if b["zone"] == zone}
    candidates = [nid for nid in snap.nodes if snap.nodes[nid] == zone]
    for nid in candidates:
        if nid in poi_ids:
            return nid
    for nid in candidates:
        if nid in booth_ids:
            return nid
    return candidates[0] if candidates else None


def dijkstra(
    snap: GraphSnapshot,
    start: str,
    goal: str,
    *,
    avoid_crowded: bool,
    avoid_closed: bool,
) -> tuple[Optional[list[str]], float]:
    """單純 Dijkstra；節點不可進入 Closed/Crowded 視 constraint 而定；blocked 邊跳過。"""
    if start == goal:
        return [start], 0.0
    if start not in snap.nodes or goal not in snap.nodes:
        return None, float("inf")

    dist: dict[str, float] = {start: 0.0}
    prev: dict[str, str] = {}
    pq: list[tuple[float, str]] = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == goal:
            break
        if d > dist.get(u, float("inf")):
            continue
        for (a, b), props in snap.edges.items():
            if a != u:
                continue
            if props.get("blocked"):
                continue
            v = b
            if v != goal:
                v_state = snap.zone_states.get(snap.nodes.get(v, ""), "")
                if _is_zone_blocked(v_state, avoid_crowded=avoid_crowded, avoid_closed=avoid_closed):
                    continue
            nd = d + props["distance"]
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))

    if goal not in dist:
        return None, float("inf")
    # reconstruct
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return path, dist[goal]


def find_route(
    snap: GraphSnapshot,
    start: str,
    goal_zones: tuple[str, ...],
    *,
    avoid_crowded: bool,
    avoid_closed: bool,
) -> tuple[Optional[list[str]], float, list[str]]:
    """
    依序到訪 goal_zones 中的每個 zone（取該 zone 的 anchor 節點），
    串接 Dijkstra 段路徑。任一段失敗 → 回傳 (None, inf, visited_zones_so_far)
    """
    full: list[str] = [start]
    total_dist = 0.0
    visited_zones: list[str] = []
    cur = start
    for z in goal_zones:
        target = _zone_anchor(snap, z)
        if target is None:
            return None, float("inf"), visited_zones
        seg, d = dijkstra(snap, cur, target, avoid_crowded=avoid_crowded, avoid_closed=avoid_closed)
        if seg is None:
            return None, float("inf"), visited_zones
        full.extend(seg[1:])
        total_dist += d
        visited_zones.append(z)
        cur = target
    return full, total_dist, visited_zones


# ---------------------------------------------------------------------------
# Plan tree helpers
# ---------------------------------------------------------------------------

def _walk_plan(plan: dict) -> list[dict]:
    out = []
    stack = [plan]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            out.append(n)
            kids = n.get("sub_plans", [])
            if isinstance(kids, list):
                stack.extend(reversed(kids))
    return out


def _atomic_actions(plan: dict) -> list[str]:
    out: list[str] = []
    for n in _walk_plan(plan):
        if n.get("is_atomic") or n.get("type") == "atomic":
            act = n.get("action") or ""
            if isinstance(act, str) and act:
                # action signature 形如 "LocateExhibit(TargetType, TargetName, ...)"
                name = act.split("(", 1)[0].strip()
                if name:
                    out.append(name)
    return out


def _is_plan_executable(plan: dict) -> tuple[bool, str]:
    if not isinstance(plan, dict):
        return False, "plan is not a dict"
    if plan.get("type") == "leaf_unresolved":
        return False, plan.get("reason", "leaf_unresolved")
    atoms = _atomic_actions(plan)
    if not atoms:
        return False, "no atomic actions"
    debug = plan.get("debug") or {}
    allowed = set(debug.get("allowed_actions") or [])
    if allowed:
        for a in atoms:
            if a not in allowed:
                return False, f"action {a} not in allowed set"
    if not any(a in NAV_ACTIONS for a in atoms):
        return False, "no navigation-class atomic action"
    return True, "ok"


# ---------------------------------------------------------------------------
# Single-case runner
# ---------------------------------------------------------------------------

@dataclass
class RunnerOptions:
    ratio: float = 0.0
    seed: int | None = None
    midway_inject: bool = True


def run_case(
    *,
    case: TestCase,
    agent_config: dict,
    kg: Neo4jBoltAdapter,
    options: RunnerOptions,
) -> CaseResult:
    """
    完整執行單一 case，回傳 CaseResult。
    
    對 KG 的副作用：
      - reset_to_baseline()
      - 透過 EventInjector 注入事件
      - 結束時不再清理（由上層 batch runner 統一處理或下一個 case 重置）
    """
    rng = random.Random(options.seed if options.seed is not None else None)
    injector = EventInjector(kg, ratio=options.ratio, rng=rng)

    avoid_crowded = "avoid_crowded" in case.constraints
    avoid_closed = "avoid_closed" in case.constraints

    # 1) baseline
    reset_to_baseline(kg)

    # 2) 初始事件（避開「終點 zone」與起終點 anchor 邊以免直接堵死）
    initial_events: list[dict] = []
    n_initial_attempts = 2  # 最多嘗試 2 次注入
    exclude = case.goal_zones
    snap0 = _snapshot_graph(kg)
    protected = (case.start_poi,) + tuple(
        a for a in (_zone_anchor(snap0, z) for z in case.goal_zones) if a
    )
    for _ in range(n_initial_attempts):
        ev = injector.inject_one(exclude_zones=exclude, protected_nodes=protected)
        if ev is not None:
            initial_events.append({"kind": ev.kind, "target": ev.target, "detail": ev.detail})

    # 3) plan_intention
    res = CaseResult(
        case_id=case.id,
        group=case.group,
        intention=case.intention,
        ratio=options.ratio,
        initial_events=initial_events,
    )

    try:
        agent = IntentionalAgent(
            agent_config=agent_config,
            intention=case.intention,
            domain_profile=EXPERIMENT_PROFILE,
        )
        plan = agent.plan_intention(case.intention)
    except Exception as e:
        res.error = f"plan_intention raised: {e!r}"
        return res

    # 4) plan executability
    atoms = _atomic_actions(plan)
    res.plan_atomic_actions = atoms
    pe_ok, pe_reason = _is_plan_executable(plan)
    res.plan_executable = pe_ok
    if not pe_ok:
        res.plan_unresolved_reason = pe_reason
        # plan 不可執行 → 直接結束
        return res

    # 5) 模擬導航：先用 baseline+initial_events 後的圖找路
    snap = _snapshot_graph(kg)
    route, dist, visited = find_route(
        snap,
        case.start_poi,
        case.goal_zones,
        avoid_crowded=avoid_crowded,
        avoid_closed=avoid_closed,
    )
    if route is None:
        # 無可行路徑（考慮 constraint）→ 任務失敗，但 plan 仍是可執行的
        res.visited_zones = visited
        res.constraint_satisfied = False
        return res

    # 6) 中途事件：模擬走到一半時再注入
    midway_events: list[dict] = []
    if options.midway_inject:
        ev = injector.inject_one(exclude_zones=case.goal_zones, protected_nodes=protected)
        if ev is not None:
            midway_events.append({"kind": ev.kind, "target": ev.target, "detail": ev.detail})
            # 中途事件可能讓現有路徑失效 → 觸發 replan
            snap2 = _snapshot_graph(kg)
            still_valid = _route_still_valid(route, snap2, avoid_crowded=avoid_crowded, avoid_closed=avoid_closed)
            if not still_valid:
                res.replan_triggered += 1
                # 從目前位置（route 中點）開始 replan
                mid = route[len(route) // 2]
                remaining_goals = case.goal_zones[
                    sum(1 for z in case.goal_zones if z in visited[: len(visited) // 2 or 1]) :
                ] or case.goal_zones
                new_seg, new_d, new_visited = find_route(
                    snap2,
                    mid,
                    remaining_goals,
                    avoid_crowded=avoid_crowded,
                    avoid_closed=avoid_closed,
                )
                if new_seg is not None:
                    res.replan_success += 1
                    # 累計新路徑距離；視為再次走完
                    dist_to_mid = _path_distance(snap2, route[: route.index(mid) + 1])
                    dist = dist_to_mid + new_d
                    visited = new_visited
                else:
                    # replan 失敗 → 任務失敗
                    res.midway_events = midway_events
                    return res

    res.midway_events = midway_events
    res.visited_zones = visited
    res.path_length_m = dist
    res.completion_time_sec = (
        dist / WALK_SPEED_MPS + REPLAN_OVERHEAD_SEC * res.replan_triggered
    )

    # 7) 評估
    res.success = (set(case.goal_zones).issubset(set(visited))) and (route is not None)
    res.intention_satisfied = visited == list(case.goal_zones)  # 順序也要對

    # 限制條件滿足度
    if avoid_closed:
        res.constraint_satisfied = res.constraint_satisfied and not _route_passes_state(
            route, snap, STATE_CLOSED
        )
    if avoid_crowded:
        res.constraint_satisfied = res.constraint_satisfied and not _route_passes_state(
            route, snap, STATE_CROWDED
        )

    return res


def _route_still_valid(
    route: list[str], snap: GraphSnapshot, *, avoid_crowded: bool, avoid_closed: bool
) -> bool:
    for i in range(len(route) - 1):
        a, b = route[i], route[i + 1]
        ed = snap.edges.get((a, b)) or snap.edges.get((b, a))
        if ed is None or ed.get("blocked"):
            return False
        zone_b = snap.nodes.get(b, "")
        st = snap.zone_states.get(zone_b, "")
        if _is_zone_blocked(st, avoid_crowded=avoid_crowded, avoid_closed=avoid_closed):
            return False
    return True


def _path_distance(snap: GraphSnapshot, path: list[str]) -> float:
    total = 0.0
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        ed = snap.edges.get((a, b)) or snap.edges.get((b, a))
        total += float(ed["distance"]) if ed else 0.0
    return total


def _route_passes_state(route: list[str], snap: GraphSnapshot, state: str) -> bool:
    for nid in route:
        z = snap.nodes.get(nid, "")
        if snap.zone_states.get(z, "") == state:
            return True
    return False

"""
Navigation Scenario：路徑規劃工具

從 Blackboard KG（Neo4j `blackboard` database）讀出當前圖譜，
以 Dijkstra 算最短路徑。支援避開：
  - `avoid_crowded`：Zone CURRENT_STATE = Crowded
  - `avoid_closed` ：Zone CURRENT_STATE = Closed
  - `avoid_blocked`：CONNECTED_TO.blocked = true（總是避開）

說明：本模組只負責「圖論計算」，不涉及 LLM 或意圖規劃。Runner 用它來
推算 baseline 路徑、注入事件後的 replan 路徑，並比對效率。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
import heapq

from src.app_helper import get_agent_config
from src.kg.adapter_neo4j import Neo4jBoltAdapter


# =============================================================================
# 圖結構
# =============================================================================
@dataclass
class GraphSnapshot:
    """從 Blackboard KG 抓出的圖快照。"""

    # node_id -> {"label": "Zone|POI|Booth", "zone": "<Zone name or None>"}
    nodes: dict[str, dict[str, str | None]] = field(default_factory=dict)
    # (a_id, b_id) -> {"distance": int, "blocked": bool}
    edges: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)
    # zone_name -> current state status_name（Normal / Crowded / Closed / Sparse）
    zone_states: dict[str, str] = field(default_factory=dict)

    def neighbors(self, node_id: str) -> list[tuple[str, dict[str, object]]]:
        return [
            (b, attrs)
            for (a, b), attrs in self.edges.items()
            if a == node_id
        ]


@dataclass
class PathResult:
    """單段（兩點之間）的最短路徑結果。"""
    start: str
    end: str
    nodes: list[str] = field(default_factory=list)   # 含起點與終點
    distance: float = float("inf")
    reachable: bool = False

    def edge_count(self) -> int:
        return max(0, len(self.nodes) - 1)


@dataclass
class MultiSegmentPath:
    """多段路徑（多步驟導航）。"""
    segments: list[PathResult] = field(default_factory=list)

    @property
    def reachable(self) -> bool:
        return bool(self.segments) and all(s.reachable for s in self.segments)

    @property
    def total_distance(self) -> float:
        if not self.reachable:
            return float("inf")
        return sum(s.distance for s in self.segments)

    def flat_nodes(self) -> list[str]:
        """串接所有段為單一 node 序列（去除接點重複）。"""
        if not self.segments:
            return []
        out = list(self.segments[0].nodes)
        for seg in self.segments[1:]:
            if not seg.nodes:
                continue
            tail = seg.nodes[1:] if out and out[-1] == seg.nodes[0] else seg.nodes
            out.extend(tail)
        return out


# =============================================================================
# 從 Neo4j 讀取圖快照
# =============================================================================
def _bb_adapter() -> Neo4jBoltAdapter:
    cfg = get_agent_config()
    kg_cfg = cfg.get("kg", {})
    merged = {**(kg_cfg.get("neo4j") or {}), **(kg_cfg.get("neo4j_blackboard") or {})}
    return Neo4jBoltAdapter.from_config(merged, logger=None)


_FETCH_NODES = """
MATCH (n)
WHERE n:Zone OR n:POI OR n:Booth
OPTIONAL MATCH (n)-[:LOCATED_IN]->(z:Zone)
WITH n,
     CASE WHEN n:Zone THEN 'Zone'
          WHEN n:POI  THEN 'POI'
          WHEN n:Booth THEN 'Booth' END AS label,
     CASE WHEN n:Zone THEN n.name ELSE z.name END AS zone_name
RETURN coalesce(n.id, n.name) AS node_id,
       label,
       zone_name
"""

_FETCH_EDGES = """
MATCH (a)-[r:CONNECTED_TO]->(b)
WHERE (a:Zone OR a:POI OR a:Booth)
  AND (b:Zone OR b:POI OR b:Booth)
RETURN coalesce(a.id, a.name) AS a_id,
       coalesce(b.id, b.name) AS b_id,
       coalesce(r.distance, 10) AS distance,
       coalesce(r.blocked, false) AS blocked
"""

_FETCH_ZONE_STATES = """
MATCH (z:Zone)-[:CURRENT_STATE]->(s:State)
RETURN z.name AS zone, s.status_name AS state
"""


def load_snapshot(adapter: Neo4jBoltAdapter | None = None) -> GraphSnapshot:
    """從 Blackboard KG 載入當前圖快照。"""
    adp = adapter or _bb_adapter()
    nodes_rows = adp.read(_FETCH_NODES)
    edges_rows = adp.read(_FETCH_EDGES)
    state_rows = adp.read(_FETCH_ZONE_STATES)

    snap = GraphSnapshot()
    for row in nodes_rows:
        nid = row["node_id"]
        if not nid:
            continue
        snap.nodes[nid] = {
            "label": row.get("label"),
            "zone": row.get("zone_name"),
        }
    for row in edges_rows:
        snap.edges[(row["a_id"], row["b_id"])] = {
            "distance": float(row.get("distance") or 10),
            "blocked": bool(row.get("blocked") or False),
        }
    for row in state_rows:
        snap.zone_states[row["zone"]] = row.get("state") or "Normal"
    return snap


# =============================================================================
# 路徑搜尋
# =============================================================================
_DEFAULT_AVOID_STATES_MAP: dict[str, frozenset[str]] = {
    "avoid_crowded": frozenset({"Crowded"}),
    "avoid_closed": frozenset({"Closed"}),
}


def _avoid_states(constraints: Iterable[str]) -> frozenset[str]:
    """根據 constraint 集合產生「應避開」的 zone state 集合。"""
    states: set[str] = set()
    for c in constraints:
        if c in _DEFAULT_AVOID_STATES_MAP:
            states.update(_DEFAULT_AVOID_STATES_MAP[c])
    return frozenset(states)


def _node_passable(
    node_id: str,
    snap: GraphSnapshot,
    *,
    avoid_states: frozenset[str],
    allow_endpoint: bool = False,
    endpoint_id: str | None = None,
) -> bool:
    """節點是否可通行。終點允許進入（即使 zone 為 Crowded/Closed）。"""
    if allow_endpoint and node_id == endpoint_id:
        return True
    meta = snap.nodes.get(node_id)
    if not meta:
        return False
    zone = meta.get("zone")
    if not zone:
        return True
    state = snap.zone_states.get(zone, "Normal")
    return state not in avoid_states


def shortest_path(
    snap: GraphSnapshot,
    start: str,
    end: str,
    *,
    constraints: Iterable[str] = (),
    avoid_blocked_always: bool = True,
) -> PathResult:
    """單段最短路徑（Dijkstra）。

    - `avoid_blocked` 在 constraints 內或 `avoid_blocked_always=True` 都會跳過 blocked edge
    - `avoid_crowded` / `avoid_closed` → 跳過對應 state 的 Zone 內部節點（終點除外）
    """
    constraints = tuple(constraints)
    avoid_states = _avoid_states(constraints)
    avoid_blocked = avoid_blocked_always or ("avoid_blocked" in constraints)

    if start not in snap.nodes or end not in snap.nodes:
        return PathResult(start=start, end=end, reachable=False)

    # Dijkstra
    dist: dict[str, float] = {start: 0.0}
    prev: dict[str, str] = {}
    pq: list[tuple[float, str]] = [(0.0, start)]
    visited: set[str] = set()

    while pq:
        d, u = heapq.heappop(pq)
        if u in visited:
            continue
        visited.add(u)
        if u == end:
            break

        for v, attrs in snap.neighbors(u):
            if v in visited:
                continue
            if avoid_blocked and attrs.get("blocked"):
                continue
            if not _node_passable(
                v, snap, avoid_states=avoid_states, allow_endpoint=True, endpoint_id=end
            ):
                continue
            edge_cost = float(attrs.get("distance", 10))
            nd = d + edge_cost
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))

    if end not in dist:
        return PathResult(start=start, end=end, reachable=False, distance=float("inf"))

    # 重建路徑
    chain = [end]
    while chain[-1] != start:
        chain.append(prev[chain[-1]])
    chain.reverse()
    return PathResult(start=start, end=end, nodes=chain, distance=dist[end], reachable=True)


def multi_segment_path(
    snap: GraphSnapshot,
    start: str,
    targets: list[str],
    *,
    constraints: Iterable[str] = (),
) -> MultiSegmentPath:
    """多步驟路徑：依序 start → t1 → t2 → ... 串接。"""
    constraints = tuple(constraints)
    result = MultiSegmentPath()
    cursor = start
    for tgt in targets:
        seg = shortest_path(snap, cursor, tgt, constraints=constraints)
        result.segments.append(seg)
        if not seg.reachable:
            break
        cursor = tgt
    return result


# =============================================================================
# 目標 id 解析（test_cases.Target → 圖中 node id）
# =============================================================================
def resolve_target_node(
    snap: GraphSnapshot,
    target_id: str,
    target_kind: str,
    *,
    start: str | None = None,
) -> str:
    """
    將 test case 中的 Target.id 對應到 Snapshot 中的真實 node id。

    - kind=poi / booth：直接使用 id。
    - kind=zone：Zone 節點沒有 CONNECTED_TO 邊，改選該 zone 內的 POI/Booth。
      若提供 `start`，優先選從起點可達且距離最短者（避免選到孤立或被 block 的點）。
    """
    if target_kind in ("poi", "booth"):
        return target_id

    candidates: list[str] = []
    for nid, meta in snap.nodes.items():
        if meta.get("zone") != target_id:
            continue
        if meta.get("label") in ("POI", "Booth"):
            candidates.append(nid)

    if start and candidates:
        best_id: str | None = None
        best_dist = float("inf")
        for nid in candidates:
            result = shortest_path(snap, start, nid)
            if result.reachable and result.distance < best_dist:
                best_dist = result.distance
                best_id = nid
        if best_id is not None:
            return best_id

    # 無 start 或皆不可達：POI 優先於 Booth，再取字典序穩定結果
    pois = sorted(n for n in candidates if snap.nodes[n].get("label") == "POI")
    booths = sorted(n for n in candidates if snap.nodes[n].get("label") == "Booth")
    if pois:
        return pois[0]
    if booths:
        return booths[0]
    return target_id


# =============================================================================
# 工具：計算 path 走完所需時間（含步行 + replan 懲罰）
# =============================================================================
def walk_time_seconds(distance_m: float, *, speed_mps: float) -> float:
    if distance_m <= 0:
        return 0.0
    return distance_m / max(0.1, speed_mps)

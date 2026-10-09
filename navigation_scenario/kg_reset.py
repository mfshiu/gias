"""
Navigation Scenario：將 Blackboard KG 還原為可導航 baseline（輕量版）

用於批次跑 60 個案例時，避免前一案的感測器注入（封鎖邊、Zone Closed）
污染下一案，導致 `baseline path unreachable`。

不會刪除節點，只還原：
  - 所有 CONNECTED_TO.blocked = false
  - 所有 Zone CURRENT_STATE → Normal
  - 所有 Booth.status = open（可選）

完整重建請仍用：`python -m navigation_scenario.seed_blackboard`
"""

from __future__ import annotations

from navigation_scenario.config import (
    BOOTHS,
    STATE_NORMAL,
    ZONES,
)
from navigation_scenario.pathing import _bb_adapter


_RESTORE_EDGES = """
MATCH ()-[r:CONNECTED_TO]->()
SET r.blocked = false,
    r.obstacle_type = null,
    r.update_source = null
"""


_PURGE_DUP_CURRENT_STATE = """
MATCH (z:Zone)-[r:CURRENT_STATE]->()
WITH z, collect(r) AS rels
WHERE size(rels) > 0
FOREACH (rel IN rels | DELETE rel)
"""

_RESTORE_ONE_ZONE = """
MATCH (z:Zone {name: $zone_name})
OPTIONAL MATCH (z)-[old:CURRENT_STATE]->()
DELETE old
WITH DISTINCT z
MATCH (st:State {status_name: $normal})
CREATE (z)-[:CURRENT_STATE {
    updated_at: datetime(),
    source: 'kg_reset'
}]->(st)
"""

_RESTORE_BOOTHS = """
UNWIND $rows AS row
MATCH (b:Booth {id: row.id})
SET b.status = 'open',
    b.update_source = 'kg_reset'
"""


def restore_navigation_baseline(
    *,
    reset_booths: bool = True,
    adapter=None,
) -> dict[str, int]:
    """
    還原導航相關的 KG 狀態，回傳簡要計數（供 log 用）。
    """
    adp = adapter or _bb_adapter()
    w = adp.write_explicit if hasattr(adp, "write_explicit") else adp.write

    w(_RESTORE_EDGES, {})
    w(_PURGE_DUP_CURRENT_STATE, {})
    for zone_name in ZONES:
        w(_RESTORE_ONE_ZONE, {"zone_name": zone_name, "normal": STATE_NORMAL})

    booth_count = 0
    if reset_booths:
        rows = [{"id": b["id"]} for b in BOOTHS]
        w(_RESTORE_BOOTHS, {"rows": rows})
        booth_count = len(rows)

    # 讀回 blocked 數確認
    blocked_rows = adp.read(
        "MATCH ()-[r:CONNECTED_TO]->() WHERE r.blocked = true RETURN count(r) AS n"
    )
    blocked_n = int(blocked_rows[0]["n"]) if blocked_rows else -1

    return {
        "zones_reset": len(ZONES),
        "booths_reset": booth_count,
        "blocked_edges_remaining": blocked_n,
    }

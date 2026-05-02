"""
Experiment 1 blackboard 種子程式

依 `experiment1.config` 內的 ZONES / POIS / BOOTHS / EDGES
建立完整 baseline 圖譜：
  States, Skills, Zones, POIs, Booths, CONNECTED_TO 邊（雙向）,
  Agents（GuideBot_01/02、SecBot_Alpha）, 預設 Zone CURRENT_STATE = Normal,
  Agent 起點 = P_Entrance / Idle。

執行：
    python -m experiment1.seed_blackboard
"""

from __future__ import annotations

import sys
import time

from src.app_helper import get_agent_config
from src.kg.adapter_neo4j import Neo4jBoltAdapter
from experiment1.config import (
    ZONES,
    POIS,
    BOOTHS,
    EDGES,
    DEFAULT_CROWD_STATE,
    STATE_CROWDED,
    STATE_NORMAL,
    STATE_SPARSE,
    STATE_CLOSED,
)


def _ensure_database_exists(base_config: dict, db_name: str) -> None:
    if db_name == "neo4j":
        return
    merged = {**base_config, "database": "system"}
    kg_sys = Neo4jBoltAdapter.from_config(merged, logger=None)
    try:
        kg_sys.write(f"CREATE DATABASE `{db_name}` IF NOT EXISTS WAIT 10 SECONDS", {})
        print(f"  [0] 已確認 database '{db_name}' 存在")
    except Exception as e:
        err = str(e).lower()
        if "exist" in err:
            pass
        elif "enterprise" in err or "community" in err or "not supported" in err:
            print(
                "  [0] Neo4j Community 僅支援單一 database。"
                "請在 gias.toml 設 [kg.neo4j_blackboard] database = \"neo4j\"",
                file=sys.stderr,
            )
            raise RuntimeError("Community Edition 不支援多 database") from e
        else:
            raise
    finally:
        kg_sys.close()


def _build_kg() -> Neo4jBoltAdapter:
    cfg = get_agent_config()
    kg_cfg = cfg.get("kg", {})
    base = kg_cfg.get("neo4j") or {}
    bb_overrides = kg_cfg.get("neo4j_blackboard") or {}
    if not base:
        raise RuntimeError("Missing [kg.neo4j] config in gias.toml")
    if not bb_overrides:
        raise RuntimeError("Missing [kg.neo4j_blackboard] config in gias.toml")
    merged = {**base, **bb_overrides}
    return Neo4jBoltAdapter.from_config(merged, logger=None)


def reset_to_baseline(kg: Neo4jBoltAdapter) -> None:
    """
    清空並重建 Blackboard 為 baseline。
    供 experiment runner 在每個 case 執行前呼叫，確保起始狀態一致。
    """
    w = kg.write_explicit if hasattr(kg, "write_explicit") else kg.write
    w("MATCH (n) DETACH DELETE n", {})

    # States
    for st in (STATE_CROWDED, STATE_NORMAL, STATE_SPARSE, STATE_CLOSED, "Idle", "Guiding", "Pending", "In_Progress"):
        w("MERGE (:State {status_name: $n})", {"n": st})

    # Skills
    for sk in ("Visitor_Navigation", "Security_Patrol"):
        w("MERGE (:Skill {name: $n})", {"n": sk})

    # Zones
    for z in ZONES:
        w("MERGE (:Zone {name: $n})", {"n": z})

    # POIs / Booths
    for poi in POIS:
        w(
            """
            MATCH (z:Zone {name: $zone})
            MERGE (p:POI {id: $id})
            SET p.name = $name
            MERGE (p)-[:LOCATED_IN]->(z)
            """,
            {"id": poi["id"], "name": poi["name"], "zone": poi["zone"]},
        )
    for b in BOOTHS:
        w(
            """
            MATCH (z:Zone {name: $zone})
            MERGE (n:Booth {id: $id})
            SET n.exhibitor = $exhibitor
            MERGE (n)-[:LOCATED_IN]->(z)
            """,
            {"id": b["id"], "exhibitor": b["exhibitor"], "zone": b["zone"]},
        )

    # Edges（雙向）
    for a_id, b_id, dist in EDGES:
        w(
            """
            MATCH (a) WHERE a.id = $a_id
            MATCH (b) WHERE b.id = $b_id
            MERGE (a)-[ra:CONNECTED_TO]->(b) SET ra.distance = $dist, ra.blocked = false
            MERGE (b)-[rb:CONNECTED_TO]->(a) SET rb.distance = $dist, rb.blocked = false
            """,
            {"a_id": a_id, "b_id": b_id, "dist": dist},
        )

    # 預設 Zone 狀態 = Normal
    for z in ZONES:
        w(
            """
            MATCH (z:Zone {name: $zone}), (st:State {status_name: $st})
            OPTIONAL MATCH (z)-[old:CURRENT_STATE]->()
            DELETE old
            CREATE (z)-[:CURRENT_STATE {updated_at: datetime()}]->(st)
            """,
            {"zone": z, "st": DEFAULT_CROWD_STATE},
        )

    # Agents
    w(
        """
        MATCH (sk:Skill {name: 'Visitor_Navigation'})
        MERGE (bot1:Agent {agent_id: 'GuideBot_01'})
        SET bot1.type = 'Guide_Robot'
        MERGE (bot1)-[:HAS_SKILL]->(sk)
        WITH bot1
        MATCH (start:POI {id: 'P_Entrance'}), (idle:State {status_name: 'Idle'})
        OPTIONAL MATCH (bot1)-[op:CURRENT_POSITION]->()
        OPTIONAL MATCH (bot1)-[os:CURRENT_STATE]->()
        DELETE op, os
        CREATE (bot1)-[:CURRENT_POSITION {updated_at: datetime()}]->(start)
        CREATE (bot1)-[:CURRENT_STATE {updated_at: datetime()}]->(idle)
        """,
        {},
    )


def _summary(kg: Neo4jBoltAdapter) -> None:
    rows = kg.query(
        """
        MATCH (z:Zone) WITH count(z) AS zones
        MATCH (p:POI) WITH zones, count(p) AS pois
        MATCH (b:Booth) WITH zones, pois, count(b) AS booths
        MATCH ()-[r:CONNECTED_TO]->() WITH zones, pois, booths, count(r) AS edges
        MATCH (a:Agent) RETURN zones, pois, booths, edges, count(a) AS agents
        """,
        {},
    )
    if rows:
        r = rows[0]
        print(
            f"  zones={r['zones']}, pois={r['pois']}, booths={r['booths']}, "
            f"edges={r['edges']}, agents={r['agents']}"
        )


def main() -> int:
    cfg = get_agent_config()
    kg_cfg = cfg.get("kg", {})
    base = kg_cfg.get("neo4j") or {}
    bb_overrides = kg_cfg.get("neo4j_blackboard") or {}
    if not base or not bb_overrides:
        print("錯誤：缺少 [kg.neo4j] 或 [kg.neo4j_blackboard] 設定。", file=sys.stderr)
        return 1
    db_name = bb_overrides.get("database", "blackboard")

    print("\n=== experiment1.seed_blackboard ===")
    print(f"  目標 database : {db_name}")
    print(f"  連線         : {base.get('uri', '?')}")

    try:
        _ensure_database_exists({**base, **bb_overrides}, db_name)
        time.sleep(1.5)
    except Exception as e:
        print(f"無法建立 / 連線 database：{e}", file=sys.stderr)
        return 1

    kg = _build_kg()
    try:
        kg.read("RETURN 1 AS ok", {})
        print("  [1] 連線 OK，開始建立 baseline 圖譜")
        reset_to_baseline(kg)
        print("  [2] baseline 已建立")
        _summary(kg)
        print("  完成。\n")
        return 0
    finally:
        kg.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""
Navigation Scenario：Blackboard KG 種子程式

依 navigation_scenario.config 的 ZONES / POIS / BOOTHS / EDGES，
建立 baseline 圖譜：
  - States（Normal/Crowded/Closed/Sparse + Idle/Guiding/Pending/In_Progress）
  - Skills（Visitor_Navigation, Security_Patrol）
  - Zones / POIs / Booths
  - CONNECTED_TO 邊（雙向，含 distance 與 blocked=false）
  - 每個 Zone 預設 CURRENT_STATE → Normal
  - Agents（GuideBot_01, GuideBot_02, SecBot_Alpha）起點 = P_Entrance / Idle

執行：
    python -m navigation_scenario.seed_blackboard

注意：本 seed 為「破壞性」操作，會清空整個 blackboard database 後重建。
"""

from __future__ import annotations

import argparse
import sys
import time

from src.app_helper import get_agent_config
from src.kg.adapter_neo4j import Neo4jBoltAdapter

from navigation_scenario.config import (
    ZONES,
    POIS,
    BOOTHS,
    EDGES,
    DEFAULT_CROWD_STATE,
    STATE_NORMAL,
    STATE_CROWDED,
    STATE_CLOSED,
    STATE_SPARSE,
)

# 超過此節點數時改走 DROP + CREATE database（秒級），不再分批 DETACH DELETE
RECREATE_NODE_THRESHOLD = 10_000


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _merged_bb_config() -> dict:
    cfg = get_agent_config()
    kg_cfg = cfg.get("kg", {})
    return {**(kg_cfg.get("neo4j") or {}), **(kg_cfg.get("neo4j_blackboard") or {})}


def _system_kg(merged: dict) -> Neo4jBoltAdapter:
    return Neo4jBoltAdapter.from_config({**merged, "database": "system"}, logger=None)


def _ensure_database_exists(base_config: dict, db_name: str) -> None:
    if db_name == "neo4j":
        return
    merged = {**base_config, "database": "system"}
    kg_sys = Neo4jBoltAdapter.from_config(merged, logger=None)
    try:
        kg_sys.write(
            f"CREATE DATABASE `{db_name}` IF NOT EXISTS WAIT 10 SECONDS", {},
        )
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
    overrides = kg_cfg.get("neo4j_blackboard") or {}
    if not base:
        raise RuntimeError("Missing [kg.neo4j] config in gias.toml")
    if not overrides:
        raise RuntimeError("Missing [kg.neo4j_blackboard] config in gias.toml")
    merged = {**base, **overrides}
    return Neo4jBoltAdapter.from_config(merged, logger=None)


def _recreate_database(db_name: str, *, merged: dict | None = None) -> None:
    """
    透過 system database 停止、刪除、重建目標 database（Neo4j 5.x 多 DB）。

    適用於累積大量節點（例如數十萬）時，比 MATCH (n) DELETE 快且穩定。
    """
    if db_name == "neo4j":
        raise RuntimeError("不允許重建預設 neo4j database，請改用其他 database 名稱。")

    merged = merged or _merged_bb_config()
    kg_sys = _system_kg(merged)
    try:
        print(f"  [1.1] 重建 database '{db_name}'（STOP → DROP → CREATE）……")
        try:
            kg_sys.write(f"STOP DATABASE `{db_name}` IF EXISTS", {})
        except Exception as e:
            err = str(e).lower()
            if "not found" not in err and "does not exist" not in err:
                print(f"    （STOP 略過：{e}）")

        kg_sys.write(f"DROP DATABASE `{db_name}` IF EXISTS", {})
        kg_sys.write(
            f"CREATE DATABASE `{db_name}` IF NOT EXISTS WAIT 30 SECONDS",
            {},
        )
        print(f"  [1.1] database '{db_name}' 已重建為空庫")
    except Exception as e:
        err = str(e).lower()
        if "enterprise" in err or "community" in err or "not supported" in err:
            raise RuntimeError(
                "Neo4j Community 不支援 DROP/CREATE DATABASE。"
                "請在 Neo4j Browser（system）手動清空，或改用 Enterprise。"
            ) from e
        raise
    finally:
        kg_sys.close()

    time.sleep(2.0)


def _purge_database(
    kg: Neo4jBoltAdapter,
    *,
    batch_size: int = 5000,
    force_recreate: bool = False,
    db_name: str = "blackboard",
) -> Neo4jBoltAdapter:
    """
    分批刪除 database 內所有節點與關係。

    背景：直接執行 `MATCH (n) DETACH DELETE n` 在資料量大時容易超時
          (Neo4j server 端 reset 連線)。改用 LIMIT 分批刪除，並在
          連線中斷時自動重連續做，最終確保節點數為 0。
    """
    merged = _merged_bb_config()

    def _ensure_kg(curr: Neo4jBoltAdapter) -> Neo4jBoltAdapter:
        try:
            curr.read("RETURN 1 AS ok", {})
            return curr
        except Exception:
            try:
                curr.close()
            except Exception:
                pass
            time.sleep(1.0)
            return Neo4jBoltAdapter.from_config(merged, logger=None)

    rows = kg.read("MATCH (n) RETURN count(n) AS c", {})
    total = int(rows[0]["c"]) if rows else 0
    if total == 0:
        print("  [1.1] 既有資料：0 → 跳過清除")
        return kg

    if force_recreate or total >= RECREATE_NODE_THRESHOLD:
        reason = "(--recreate)" if force_recreate else f">= {RECREATE_NODE_THRESHOLD}"
        print(
            f"  [1.1] 既有資料：{total} 個節點 {reason} "
            f"→ 改為 DROP/CREATE database（請勿用分批刪除）"
        )
        try:
            kg.close()
        except Exception:
            pass
        _recreate_database(db_name, merged=merged)
        return _build_kg()

    print(f"  [1.1] 既有資料：{total} 個節點 → 分批刪除（batch={batch_size}）")
    deleted_rounds = 0
    while True:
        try:
            kg.write(
                "MATCH (n) WITH n LIMIT $n DETACH DELETE n",
                {"n": batch_size},
            )
        except Exception as e:
            print(f"    ! 批次刪除失敗（{e.__class__.__name__}），重連後續做……")
            kg = _ensure_kg(kg)
            continue

        deleted_rounds += 1
        rows = kg.read("MATCH (n) RETURN count(n) AS c", {})
        remaining = int(rows[0]["c"]) if rows else 0
        print(f"      round {deleted_rounds}: 剩餘 {remaining} 個節點")
        if remaining == 0:
            break
    return kg


# -----------------------------------------------------------------------------
# 主要 reset 流程：可被 runner 在每個 case 執行前呼叫
# -----------------------------------------------------------------------------
def reset_to_baseline(
    kg: Neo4jBoltAdapter,
    *,
    force_recreate: bool = False,
    db_name: str = "blackboard",
) -> Neo4jBoltAdapter:
    """
    清空並重建 Blackboard 為 baseline 狀態。

    回傳：可能因 DROP/CREATE 或重連而換新的 kg 實例；呼叫端請以回傳值為準。
    """
    kg = _purge_database(kg, force_recreate=force_recreate, db_name=db_name)

    w = kg.write_explicit if hasattr(kg, "write_explicit") else kg.write

    for st in (
        STATE_NORMAL, STATE_CROWDED, STATE_CLOSED, STATE_SPARSE,
        "Idle", "Guiding", "Pending", "In_Progress",
    ):
        w("MERGE (:State {status_name: $n})", {"n": st})

    for sk in ("Visitor_Navigation", "Security_Patrol"):
        w("MERGE (:Skill {name: $n})", {"n": sk})

    for z in ZONES:
        w("MERGE (:Zone {name: $n})", {"n": z})

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

    for a_id, b_id, dist in EDGES:
        w(
            """
            MATCH (a) WHERE a.id = $a_id
            MATCH (b) WHERE b.id = $b_id
            MERGE (a)-[ra:CONNECTED_TO]->(b)
              SET ra.distance = $dist, ra.blocked = false
            MERGE (b)-[rb:CONNECTED_TO]->(a)
              SET rb.distance = $dist, rb.blocked = false
            """,
            {"a_id": a_id, "b_id": b_id, "dist": dist},
        )

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

    # Guide Bots：起點 = P_Entrance / 狀態 = Idle
    for bot_id in ("GuideBot_01", "GuideBot_02"):
        w(
            """
            MATCH (sk:Skill {name: 'Visitor_Navigation'})
            MERGE (bot:Agent {agent_id: $bot_id})
            SET bot.type = 'Guide_Robot'
            MERGE (bot)-[:HAS_SKILL]->(sk)
            WITH bot
            MATCH (start:POI {id: 'P_Entrance'}),
                  (idle:State {status_name: 'Idle'})
            OPTIONAL MATCH (bot)-[op:CURRENT_POSITION]->()
            OPTIONAL MATCH (bot)-[os:CURRENT_STATE]->()
            DELETE op, os
            CREATE (bot)-[:CURRENT_POSITION {updated_at: datetime()}]->(start)
            CREATE (bot)-[:CURRENT_STATE {updated_at: datetime()}]->(idle)
            """,
            {"bot_id": bot_id},
        )

    # Security Bot：起點 = P_Info / 狀態 = Idle
    w(
        """
        MATCH (sk:Skill {name: 'Security_Patrol'})
        MERGE (bot:Agent {agent_id: 'SecBot_Alpha'})
        SET bot.type = 'Security_Robot'
        MERGE (bot)-[:HAS_SKILL]->(sk)
        WITH bot
        MATCH (start:POI {id: 'P_Info'}),
              (idle:State {status_name: 'Idle'})
        OPTIONAL MATCH (bot)-[op:CURRENT_POSITION]->()
        OPTIONAL MATCH (bot)-[os:CURRENT_STATE]->()
        DELETE op, os
        CREATE (bot)-[:CURRENT_POSITION {updated_at: datetime()}]->(start)
        CREATE (bot)-[:CURRENT_STATE {updated_at: datetime()}]->(idle)
        """,
        {},
    )

    return kg


def _summary(kg: Neo4jBoltAdapter) -> None:
    rows = kg.query(
        """
        MATCH (z:Zone)                          WITH count(z) AS zones
        MATCH (p:POI)                           WITH zones, count(p) AS pois
        MATCH (b:Booth)                         WITH zones, pois, count(b) AS booths
        MATCH ()-[r:CONNECTED_TO]->()           WITH zones, pois, booths, count(r) AS edges
        MATCH (a:Agent)                         RETURN zones, pois, booths, edges, count(a) AS agents
        """,
        {},
    )
    if rows:
        r = rows[0]
        print(
            f"  zones={r['zones']}, pois={r['pois']}, booths={r['booths']}, "
            f"edges={r['edges']} (directed), agents={r['agents']}"
        )


# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="建立 Navigation Scenario 的 Blackboard KG baseline",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="強制 DROP + CREATE blackboard database（不論節點數多少）",
    )
    args = parser.parse_args(argv)

    cfg = get_agent_config()
    kg_cfg = cfg.get("kg", {})
    base = kg_cfg.get("neo4j") or {}
    overrides = kg_cfg.get("neo4j_blackboard") or {}
    if not base or not overrides:
        print(
            "錯誤：缺少 [kg.neo4j] 或 [kg.neo4j_blackboard] 設定。",
            file=sys.stderr,
        )
        return 1

    db_name = overrides.get("database", "blackboard")
    print("\n=== navigation_scenario.seed_blackboard ===")
    print(f"  目標 database : {db_name}")
    print(f"  連線         : {base.get('uri', '?')}")
    if args.recreate:
        print("  模式         : --recreate（強制重建 database）")

    merged = {**base, **overrides}
    try:
        if not args.recreate:
            _ensure_database_exists(merged, db_name)
            time.sleep(1.0)
    except Exception as e:
        print(f"無法建立 / 連線 database：{e}", file=sys.stderr)
        return 1

    kg = _build_kg()
    try:
        kg.read("RETURN 1 AS ok", {})
        print("  [1] 連線 OK，開始建立 baseline 圖譜")
        kg = reset_to_baseline(kg, force_recreate=args.recreate, db_name=db_name)
        print("  [2] baseline 已建立")
        _summary(kg)
        print("  [✓] 完成。\n")
        return 0
    finally:
        try:
            kg.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

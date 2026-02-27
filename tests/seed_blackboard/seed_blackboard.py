# tests/seed_blackboard/seed_blackboard.py
"""
展場 Blackboard 圖譜種子工具

一次建立完整圖譜：環境本體（States、Skills、Zones、POI、Booth、CONNECTED_TO、Agents）
與即時脈絡（Zone 人潮、Agent 位置與狀態、導航任務）。

使用 [kg.neo4j] 連線同一 Neo4j，[kg.neo4j_blackboard] 僅覆寫 database。

執行：python -m tests.seed_blackboard.seed_blackboard
"""

from __future__ import annotations

from src.app_helper import get_agent_config
from src.kg.adapter_neo4j import Neo4jBoltAdapter


def _get_kg() -> Neo4jBoltAdapter:
    cfg = get_agent_config()
    kg_cfg = cfg.get("kg", {})
    base = kg_cfg.get("neo4j")
    bb_overrides = kg_cfg.get("neo4j_blackboard")
    if not isinstance(base, dict):
        raise RuntimeError("Missing [kg.neo4j] config in gias.toml")
    if not isinstance(bb_overrides, dict):
        raise RuntimeError("Missing [kg.neo4j_blackboard] config in gias.toml")
    # 同一 Neo4j 實例，僅 database 不同
    merged = {**base, **bb_overrides}
    return Neo4jBoltAdapter.from_config(merged, logger=None)


def run(kg: Neo4jBoltAdapter) -> None:
    """一次處理完畢：清空後建立完整 Blackboard 圖譜"""
    # 1. 清空
    kg.write("MATCH (n) DETACH DELETE n", {})
    print("  [1] 已清空圖譜")

    # 2. States
    kg.write(
        """
        CREATE (:State {status_name: 'Idle'}),
               (:State {status_name: 'Guiding'}),
               (:State {status_name: 'Charging'}),
               (:State {status_name: 'Crowded'}),
               (:State {status_name: 'Normal'}),
               (:State {status_name: 'Pending'}),
               (:State {status_name: 'In_Progress'})
        """,
        {},
    )
    print("  [2] 已建立 States")

    # 3. Skills
    kg.write(
        """
        CREATE (:Skill {name: 'Visitor_Navigation'}),
               (:Skill {name: 'Security_Patrol'})
        """,
        {},
    )
    print("  [3] 已建立 Skills")

    # 4. Zones
    kg.write(
        """
        CREATE (z_main:Zone {name: 'Main_Hall'}),
               (z_ai:Zone {name: 'AI_Tech_Area'}),
               (z_game:Zone {name: 'Gaming_Area'})
        """,
        {},
    )
    print("  [4] 已建立 Zones")

    # 5. POI 與 Booth
    kg.write(
        """
        MATCH (z_main:Zone {name: 'Main_Hall'})
        CREATE (entrance:POI {id: 'P_Entrance', name: 'Main Entrance'})-[:LOCATED_IN]->(z_main),
               (info_desk:POI {id: 'P_Info', name: 'Information Desk'})-[:LOCATED_IN]->(z_main)
        """,
        {},
    )
    kg.write(
        """
        MATCH (z_ai:Zone {name: 'AI_Tech_Area'})
        CREATE (booth_a1:Booth {id: 'B_A1', exhibitor: 'TechCorp AI'})-[:LOCATED_IN]->(z_ai),
               (booth_a2:Booth {id: 'B_A2', exhibitor: 'Robotics Inc'})-[:LOCATED_IN]->(z_ai)
        """,
        {},
    )
    kg.write(
        """
        MATCH (z_game:Zone {name: 'Gaming_Area'})
        CREATE (booth_b1:Booth {id: 'B_B1', exhibitor: 'GameStudio X'})-[:LOCATED_IN]->(z_game),
               (restroom:POI {id: 'P_Restroom', name: 'Restroom_South'})-[:LOCATED_IN]->(z_game)
        """,
        {},
    )
    print("  [5] 已建立 POI 與 Booth")

    # 6. CONNECTED_TO
    for a_id, b_id, dist in [
        ("P_Entrance", "P_Info", 10),
        ("P_Info", "B_A1", 20),
        ("B_A1", "B_A2", 15),
        ("P_Info", "B_B1", 30),
        ("B_B1", "P_Restroom", 10),
        ("B_A2", "B_B1", 25),
    ]:
        kg.write(
            """
            MATCH (a) WHERE a.id = $a_id
            MATCH (b) WHERE b.id = $b_id
            CREATE (a)-[:CONNECTED_TO {distance: $dist}]->(b),
                   (b)-[:CONNECTED_TO {distance: $dist}]->(a)
            """,
            {"a_id": a_id, "b_id": b_id, "dist": dist},
        )
    print("  [6] 已建立 CONNECTED_TO 路徑")

    # 7. Agents
    kg.write(
        """
        MATCH (sk:Skill {name: 'Visitor_Navigation'})
        CREATE (bot1:Agent {agent_id: 'GuideBot_01', type: 'Guide_Robot'})-[:HAS_SKILL]->(sk),
               (bot2:Agent {agent_id: 'GuideBot_02', type: 'Guide_Robot'})-[:HAS_SKILL]->(sk)
        """,
        {},
    )
    kg.write(
        """
        MATCH (sk:Skill {name: 'Security_Patrol'})
        CREATE (sec_bot:Agent {agent_id: 'SecBot_Alpha', type: 'Security_Robot'})-[:HAS_SKILL]->(sk)
        """,
        {},
    )
    print("  [7] 已建立 Agents")

    # 8. Zone 人潮狀態
    kg.write(
        """
        MATCH (z_ai:Zone {name: 'AI_Tech_Area'}), (st:State {status_name: 'Crowded'})
        CREATE (z_ai)-[:CURRENT_STATE {updated_at: datetime()}]->(st)
        """,
        {},
    )
    kg.write(
        """
        MATCH (z_game:Zone {name: 'Gaming_Area'}), (st:State {status_name: 'Normal'})
        CREATE (z_game)-[:CURRENT_STATE {updated_at: datetime()}]->(st)
        """,
        {},
    )
    print("  [8] 已更新 Zone 人潮狀態")

    # 9. Agent 位置與狀態
    kg.write(
        """
        MATCH (bot1:Agent {agent_id: 'GuideBot_01'}), (entrance:POI {id: 'P_Entrance'})
        MATCH (st_idle:State {status_name: 'Idle'})
        CREATE (bot1)-[:CURRENT_POSITION {updated_at: datetime()}]->(entrance),
               (bot1)-[:CURRENT_STATE {updated_at: datetime()}]->(st_idle)
        """,
        {},
    )
    kg.write(
        """
        MATCH (bot2:Agent {agent_id: 'GuideBot_02'}), (booth_a2:Booth {id: 'B_A2'})
        MATCH (st_guiding:State {status_name: 'Guiding'})
        CREATE (bot2)-[:CURRENT_POSITION {updated_at: datetime()}]->(booth_a2),
               (bot2)-[:CURRENT_STATE {updated_at: datetime()}]->(st_guiding)
        """,
        {},
    )
    print("  [9] 已更新 Agent 位置與狀態")

    # 10. 導航任務
    kg.write(
        """
        MATCH (info_desk:POI {id: 'P_Info'}), (target_booth:Booth {id: 'B_B1'})
        MATCH (st_pending:State {status_name: 'Pending'}), (sk_nav:Skill {name: 'Visitor_Navigation'})
        CREATE (t_nav:Task {task_id: 'TASK_NAV_001', type: 'Visitor_Guidance', priority: 'High'})
        CREATE (t_nav)-[:HAS_STATUS {updated_at: datetime()}]->(st_pending)
        CREATE (t_nav)-[:REQUIRES_SKILL]->(sk_nav)
        CREATE (t_nav)-[:START_LOCATION]->(info_desk)
        CREATE (t_nav)-[:TARGET_LOCATION]->(target_booth)
        """,
        {},
    )
    print("  [10] 已建立導航任務 TASK_NAV_001")


def main() -> None:
    kg = _get_kg()
    try:
        print("\n=== seed_blackboard ===")
        run(kg)
        print("  完成。\n")
    finally:
        kg.close()


if __name__ == "__main__":
    main()

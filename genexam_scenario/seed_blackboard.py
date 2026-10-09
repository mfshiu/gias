"""
GenExam Scenario：Blackboard KG 種子程式

依 genexam_scenario.config 的 TOPICS / SOURCES / BLOOM_LEVELS / DIFFICULTY_LEVELS /
QUESTION_TYPES / AGENTS / AGENT_STATES / SKILLS，
建立 baseline 圖譜，由「領域知識子圖」與「生成狀態子圖」組成：

【領域知識子圖（KG-RAG 來源）】
  (:Topic)-[:HAS_SUBTOPIC]->(:Subtopic)
  (:Subtopic)-[:HAS_CONCEPT]->(:Concept)
  (:Concept)-[:SUPPORTED_BY]->(:Fact)
  (:Fact)-[:CITED_FROM]->(:Source)

【生成狀態子圖（GVR 工作空間）】
  (:BloomLevel) / (:Difficulty) / (:QuestionType)   ← 約束分類學
  (:State) / (:Skill)                                ← Agent 工作狀態 / 技能
  (:Agent)-[:HAS_SKILL]->(:Skill)
  (:Agent)-[:CURRENT_STATE]->(:State)

執行：
    python -m genexam_scenario.seed_blackboard

注意：本 seed 為「破壞性」操作，會清空整個 blackboard database 後重建。
      runner.py 在每個 case 開始前可呼叫 reset_to_baseline() 還原。
"""

from __future__ import annotations

import argparse
import sys
import time

from src.app_helper import get_agent_config
from src.kg.adapter_neo4j import Neo4jBoltAdapter

from genexam_scenario.config import (
    TOPICS,
    SOURCES,
    BLOOM_LEVELS,
    DIFFICULTY_LEVELS,
    QUESTION_TYPES,
    AGENTS,
    AGENT_STATES,
    SKILLS,
    total_counts,
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
    """透過 system database 停止、刪除、重建目標 database（Neo4j 5.x 多 DB）。"""
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
    """分批刪除 database 內所有節點與關係。"""
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
    清空並重建 Blackboard 為 baseline 狀態：
      - 領域知識子圖（Topic → Subtopic → Concept → Fact → Source）
      - 約束分類學（Difficulty / BloomLevel / QuestionType）
      - Agent 工作子圖（Agent / State / Skill）

    回傳：可能因 DROP/CREATE 或重連而換新的 kg 實例；呼叫端請以回傳值為準。
    """
    kg = _purge_database(kg, force_recreate=force_recreate, db_name=db_name)
    w = kg.write_explicit if hasattr(kg, "write_explicit") else kg.write

    # -------------------------------------------------------------------------
    # 1) 約束分類學（Difficulty / BloomLevel / QuestionType）
    # -------------------------------------------------------------------------
    for d in DIFFICULTY_LEVELS:
        w("MERGE (:Difficulty {name:$n})", {"n": d})

    for b in BLOOM_LEVELS:
        w(
            "MERGE (bl:BloomLevel {level:$lv}) SET bl.name = $n",
            {"lv": int(b["level"]), "n": b["name"]},
        )

    for qt in QUESTION_TYPES:
        w("MERGE (:QuestionType {name:$n})", {"n": qt})

    # -------------------------------------------------------------------------
    # 2) Agent 工作狀態與技能
    # -------------------------------------------------------------------------
    for st in AGENT_STATES:
        w("MERGE (:State {status_name:$n})", {"n": st})

    for sk in SKILLS:
        w("MERGE (:Skill {name:$n})", {"n": sk})

    for ag in AGENTS:
        w(
            """
            MERGE (a:Agent {agent_id:$aid})
            SET a.type = $type
            """,
            {"aid": ag["agent_id"], "type": ag["type"]},
        )
        for sk_name in ag.get("skills", ()):
            w(
                """
                MATCH (a:Agent {agent_id:$aid}),
                      (s:Skill {name:$sn})
                MERGE (a)-[:HAS_SKILL]->(s)
                """,
                {"aid": ag["agent_id"], "sn": sk_name},
            )
        # 預設狀態 = Idle
        w(
            """
            MATCH (a:Agent {agent_id:$aid}),
                  (st:State {status_name:'Idle'})
            OPTIONAL MATCH (a)-[old:CURRENT_STATE]->()
            DELETE old
            CREATE (a)-[:CURRENT_STATE {updated_at: datetime()}]->(st)
            """,
            {"aid": ag["agent_id"]},
        )

    # -------------------------------------------------------------------------
    # 3) Source 來源（為 Fact 提供 grounding 依據）
    # -------------------------------------------------------------------------
    for s in SOURCES:
        w(
            """
            MERGE (src:Source {id:$id})
            SET src.title = $title,
                src.year = $year,
                src.url = $url,
                src.credibility = $cred
            """,
            {
                "id": s["id"],
                "title": s["title"],
                "year": int(s["year"]),
                "url": s["url"],
                "cred": float(s["credibility"]),
            },
        )

    # -------------------------------------------------------------------------
    # 4) 領域知識子圖（Topic / Subtopic / Concept / Fact）
    # -------------------------------------------------------------------------
    for t in TOPICS:
        w(
            """
            MERGE (t:Topic {id:$id})
            SET t.name = $name, t.name_zh = $name_zh
            """,
            {"id": t["id"], "name": t["name"], "name_zh": t["name_zh"]},
        )
        for s in t.get("subtopics", []):
            w(
                """
                MERGE (st:Subtopic {id:$id})
                SET st.name = $name, st.name_zh = $name_zh
                WITH st
                MATCH (t:Topic {id:$tid})
                MERGE (t)-[:HAS_SUBTOPIC]->(st)
                """,
                {
                    "id": s["id"],
                    "name": s["name"],
                    "name_zh": s["name_zh"],
                    "tid": t["id"],
                },
            )
            for c in s.get("concepts", []):
                w(
                    """
                    MERGE (c:Concept {id:$id})
                    SET c.name = $name,
                        c.name_zh = $name_zh,
                        c.definition = $defn
                    WITH c
                    MATCH (st:Subtopic {id:$sid})
                    MERGE (st)-[:HAS_CONCEPT]->(c)
                    """,
                    {
                        "id": c["id"],
                        "name": c["name"],
                        "name_zh": c["name_zh"],
                        "defn": c.get("definition", ""),
                        "sid": s["id"],
                    },
                )
                for f in c.get("facts", []):
                    w(
                        """
                        MERGE (f:Fact {id:$id})
                        SET f.statement = $stmt,
                            f.source_id = $src
                        WITH f
                        MATCH (c:Concept {id:$cid})
                        MERGE (c)-[:SUPPORTED_BY]->(f)
                        WITH f
                        MATCH (src:Source {id:$src})
                        MERGE (f)-[:CITED_FROM]->(src)
                        """,
                        {
                            "id": f["id"],
                            "stmt": f["statement"],
                            "src": f["source_id"],
                            "cid": c["id"],
                        },
                    )

    return kg


# -----------------------------------------------------------------------------
# 摘要
# -----------------------------------------------------------------------------
def _summary(kg: Neo4jBoltAdapter) -> None:
    rows = kg.query(
        """
        MATCH (t:Topic)              WITH count(t)  AS topics
        MATCH (s:Subtopic)           WITH topics, count(s) AS subtopics
        MATCH (c:Concept)            WITH topics, subtopics, count(c) AS concepts
        MATCH (f:Fact)               WITH topics, subtopics, concepts, count(f) AS facts
        MATCH (src:Source)           WITH topics, subtopics, concepts, facts, count(src) AS sources
        MATCH (a:Agent)              WITH topics, subtopics, concepts, facts, sources, count(a) AS agents
        MATCH (st:State)             WITH topics, subtopics, concepts, facts, sources, agents, count(st) AS states
        MATCH (sk:Skill)             WITH topics, subtopics, concepts, facts, sources, agents, states, count(sk) AS skills
        MATCH (bl:BloomLevel)        WITH topics, subtopics, concepts, facts, sources, agents, states, skills, count(bl) AS blooms
        MATCH (d:Difficulty)         WITH topics, subtopics, concepts, facts, sources, agents, states, skills, blooms, count(d) AS diffs
        MATCH (qt:QuestionType)      RETURN topics, subtopics, concepts, facts, sources,
                                            agents, states, skills, blooms, diffs, count(qt) AS qtypes
        """,
        {},
    )
    if rows:
        r = rows[0]
        print(
            f"  topics={r['topics']}, subtopics={r['subtopics']}, "
            f"concepts={r['concepts']}, facts={r['facts']}, sources={r['sources']}"
        )
        print(
            f"  bloom_levels={r['blooms']}, difficulties={r['diffs']}, "
            f"question_types={r['qtypes']}"
        )
        print(
            f"  agents={r['agents']}, states={r['states']}, skills={r['skills']}"
        )


# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="建立 GenExam Scenario 的 Blackboard KG baseline",
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
    print("\n=== genexam_scenario.seed_blackboard ===")
    print(f"  目標 database : {db_name}")
    print(f"  連線         : {base.get('uri', '?')}")
    if args.recreate:
        print("  模式         : --recreate（強制重建 database）")

    expected = total_counts()
    print(
        f"  baseline 預期 : topics={expected['topics']}, "
        f"subtopics={expected['subtopics']}, concepts={expected['concepts']}, "
        f"facts={expected['facts']}, sources={expected['sources']}"
    )

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

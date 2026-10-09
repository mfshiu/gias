"""
GenExam Scenario：Action KG 種子程式

將 11 個試題生成相關 actions（含 embedding）寫入
[kg.neo4j_actions] 指向的 Neo4j database（預設 'actions'）。

涵蓋 GVR 閉環的所有角色：
  Coordinator → PlanGeneration, FinalizeQuestionSet, UpdateIntention
  Generator   → GenerateQuestion, RetrieveKnowledge
  Verifier    → AssessDifficulty, AssessBloomLevel, ClassifyQuestionType,
                VerifyKnowledgeGrounding, VerifyConstraintSet
  Refiner     → RefineQuestion

執行：
    python -m genexam_scenario.seed_actions

注意：本 seed 為「破壞性」操作，會清空 actions database 中既有的
      Action / Param 節點再重新寫入。
"""

from __future__ import annotations

import json
import sys
import time
import uuid

from src.app_helper import get_agent_config
from src.kg.adapter_neo4j import Neo4jBoltAdapter
from src.llm.client import LLMClient


# -----------------------------------------------------------------------------
# 試題生成專用 Action 定義
# -----------------------------------------------------------------------------
ACTIONS: list[dict] = [
    # ----- Coordinator -------------------------------------------------------
    {
        "id": "corr-uuid-plan-generation",
        "name": "Plan Generation",
        "desc": "依使用者請求的主題、題數與約束，規劃多步生成計畫並建立 GenerationSession 與 Intention 節點",
        "topic": "qgen.plan",
        "task": "PlanGeneration",
        "params": [
            {"key": "request_text", "name": "原始請求",
             "desc": "使用者的自然語言請求",
             "type": "string", "required": True,
             "example": "請生成 5 題關於空氣污染的中等難度選擇題"},
            {"key": "topic", "name": "主題",
             "desc": "對應 KG-RAG 的領域主題 id 或名稱",
             "type": "string", "required": True, "example": "air_pollution"},
            {"key": "count", "name": "題數",
             "desc": "需要產生的題目數量",
             "type": "int", "required": True, "example": 5},
            {"key": "constraints", "name": "起始約束",
             "desc": "起始限制條件（difficulty/bloom_level/question_type 等）",
             "type": "json", "required": True,
             "example": {"difficulty": "medium", "question_type": "mcq"}},
            {"key": "multi_step", "name": "多步生成",
             "desc": "是否採多步生成（先草稿、再 verify、再 refine）",
             "type": "bool", "required": False, "example": True},
            {"key": "session_id", "name": "生成會話 ID",
             "desc": "本次生成任務的會話識別碼",
             "type": "string", "required": True, "example": "qgen_20260524_001"},
        ],
    },
    {
        "id": "corr-uuid-update-intention",
        "name": "Update Intention",
        "desc": "在生成中途接收到動態事件時更新意圖與約束（新增 / 收緊 / 替換 / 鬆綁），同時保留原始意圖以利穩定性評估",
        "topic": "qgen.intention",
        "task": "UpdateIntention",
        "params": [
            {"key": "session_id", "name": "會話 ID",
             "desc": "目標生成會話", "type": "string",
             "required": True, "example": "qgen_20260524_001"},
            {"key": "delta_constraints", "name": "約束變更",
             "desc": "本次新增或變更的約束 (key, value, op)",
             "type": "json", "required": True,
             "example": {"difficulty": "hard", "_op": "tighten"}},
            {"key": "injection_phase", "name": "注入時機",
             "desc": "initial / mid",
             "type": "enum", "required": True,
             "enum": ["initial", "mid"], "example": "mid"},
            {"key": "keep_original", "name": "保留原意圖",
             "desc": "是否保留原 Intention 作為穩定性基準",
             "type": "bool", "required": False, "example": True},
        ],
    },
    {
        "id": "corr-uuid-finalize-set",
        "name": "Finalize Question Set",
        "desc": "彙整通過驗證的題目，輸出最終題組並把 GenerationSession 標記為 Finalized",
        "topic": "qgen.finalize",
        "task": "FinalizeQuestionSet",
        "params": [
            {"key": "session_id", "name": "會話 ID",
             "desc": "目標生成會話", "type": "string",
             "required": True, "example": "qgen_20260524_001"},
            {"key": "min_pass_count", "name": "最少通過題數",
             "desc": "至少需要多少題通過 verifier 才能 finalize",
             "type": "int", "required": True, "example": 5},
            {"key": "include_metadata", "name": "包含後設資料",
             "desc": "輸出是否附帶 grounding refs / verification log",
             "type": "bool", "required": False, "example": True},
        ],
    },

    # ----- Generator ---------------------------------------------------------
    {
        "id": "corr-uuid-retrieve-knowledge",
        "name": "Retrieve Knowledge",
        "desc": "從領域知識子圖（Topic/Subtopic/Concept/Fact）中檢索與主題相關的概念與事實作為命題素材",
        "topic": "qgen.retrieve",
        "task": "RetrieveKnowledge",
        "params": [
            {"key": "topic", "name": "主題",
             "desc": "領域主題 id 或名稱",
             "type": "string", "required": True, "example": "waste_management"},
            {"key": "subtopic", "name": "子主題",
             "desc": "可選的子主題，用以聚焦檢索",
             "type": "string", "required": False, "example": "circular_economy"},
            {"key": "top_k", "name": "Top K",
             "desc": "回傳前 K 個相關 concept",
             "type": "int", "required": False, "example": 5},
            {"key": "bloom_level", "name": "Bloom 等級",
             "desc": "Bloom 層級，影響擷取概念抽象度",
             "type": "int", "required": False, "example": 4},
        ],
    },
    {
        "id": "corr-uuid-generate-question",
        "name": "Generate Question",
        "desc": "依主題、難度、Bloom 層級與題型限制，結合知識檢索結果產出單一題目（含選項與答案）",
        "topic": "qgen.generate",
        "task": "GenerateQuestion",
        "params": [
            {"key": "session_id", "name": "會話 ID",
             "desc": "歸屬的生成會話", "type": "string",
             "required": True, "example": "qgen_20260524_001"},
            {"key": "topic", "name": "主題",
             "desc": "領域主題", "type": "string",
             "required": True, "example": "climate_change"},
            {"key": "difficulty", "name": "難度",
             "desc": "easy / medium / hard",
             "type": "enum", "required": True,
             "enum": ["easy", "medium", "hard"], "example": "hard"},
            {"key": "bloom_level", "name": "Bloom 層級",
             "desc": "1=Remember, 6=Create",
             "type": "int", "required": True, "example": 4},
            {"key": "question_type", "name": "題型",
             "desc": "mcq / true_false / short_answer / cloze",
             "type": "enum", "required": True,
             "enum": ["mcq", "true_false", "short_answer", "cloze"],
             "example": "mcq"},
            {"key": "knowledge_refs", "name": "知識依據",
             "desc": "本題綁定的 Concept / Fact id 清單",
             "type": "list[string]", "required": True,
             "example": ["C_CO2", "F_CO2_1"]},
            {"key": "attempt_no", "name": "嘗試次數",
             "desc": "第幾次嘗試（refine 後遞增）",
             "type": "int", "required": False, "example": 1},
        ],
    },

    # ----- Verifier ----------------------------------------------------------
    {
        "id": "corr-uuid-assess-difficulty",
        "name": "Assess Difficulty",
        "desc": "評估題目實際難度是否符合 Intention 指定的難度等級",
        "topic": "qgen.verify",
        "task": "AssessDifficulty",
        "params": [
            {"key": "question_id", "name": "題目 ID",
             "desc": "要評估的題目",
             "type": "string", "required": True, "example": "q_001"},
            {"key": "expected", "name": "預期難度",
             "desc": "Intention 指定的難度",
             "type": "enum", "required": True,
             "enum": ["easy", "medium", "hard"], "example": "hard"},
            {"key": "tolerance", "name": "容忍度",
             "desc": "允許偏離預期等級的階數",
             "type": "int", "required": False, "example": 0},
        ],
    },
    {
        "id": "corr-uuid-assess-bloom",
        "name": "Assess Bloom Level",
        "desc": "判定題目所要求的認知層次是否符合 Intention 指定的 Bloom Level",
        "topic": "qgen.verify",
        "task": "AssessBloomLevel",
        "params": [
            {"key": "question_id", "name": "題目 ID",
             "desc": "要評估的題目",
             "type": "string", "required": True, "example": "q_001"},
            {"key": "expected_level", "name": "預期 Bloom 等級",
             "desc": "1–6",
             "type": "int", "required": True, "example": 4},
            {"key": "tolerance", "name": "容忍度",
             "desc": "允許偏離預期 Bloom 等級的階數",
             "type": "int", "required": False, "example": 0},
        ],
    },
    {
        "id": "corr-uuid-classify-qtype",
        "name": "Classify Question Type",
        "desc": "確認題目格式（選項數量、是非結構、簡答長度等）是否符合預期題型",
        "topic": "qgen.verify",
        "task": "ClassifyQuestionType",
        "params": [
            {"key": "question_id", "name": "題目 ID",
             "desc": "要驗證的題目",
             "type": "string", "required": True, "example": "q_001"},
            {"key": "expected_type", "name": "預期題型",
             "desc": "mcq / true_false / short_answer / cloze",
             "type": "enum", "required": True,
             "enum": ["mcq", "true_false", "short_answer", "cloze"],
             "example": "mcq"},
        ],
    },
    {
        "id": "corr-uuid-verify-grounding",
        "name": "Verify Knowledge Grounding",
        "desc": "檢查題目陳述與答案是否能在 Blackboard 領域知識子圖（Concept / Fact）找到支持，避免幻覺",
        "topic": "qgen.verify",
        "task": "VerifyKnowledgeGrounding",
        "params": [
            {"key": "question_id", "name": "題目 ID",
             "desc": "要驗證的題目",
             "type": "string", "required": True, "example": "q_001"},
            {"key": "knowledge_refs", "name": "知識依據",
             "desc": "宣稱對應的 Concept / Fact id",
             "type": "list[string]", "required": True,
             "example": ["C_PM25", "F_PM25_1"]},
            {"key": "min_confidence", "name": "最低信心",
             "desc": "通過的最低 grounding 分數",
             "type": "float", "required": False, "example": 0.7},
        ],
    },
    {
        "id": "corr-uuid-verify-constraintset",
        "name": "Verify Constraint Set",
        "desc": "在批次層級檢查整組題目是否同時滿足 Intention 的所有約束（難度 + Bloom + 題型 + 題數）",
        "topic": "qgen.verify",
        "task": "VerifyConstraintSet",
        "params": [
            {"key": "session_id", "name": "會話 ID",
             "desc": "要驗證的生成會話",
             "type": "string", "required": True, "example": "qgen_20260524_001"},
            {"key": "constraint_keys", "name": "檢查的約束 keys",
             "desc": "要納入檢查的約束（'difficulty', 'bloom_level', 'question_type', 'count'...）",
             "type": "list[string]", "required": True,
             "example": ["difficulty", "bloom_level", "question_type", "count"]},
            {"key": "strict", "name": "嚴格模式",
             "desc": "嚴格模式下任何不符即整批失敗",
             "type": "bool", "required": False, "example": True},
        ],
    },

    # ----- Refiner -----------------------------------------------------------
    {
        "id": "corr-uuid-refine-question",
        "name": "Refine Question",
        "desc": "依 Verifier 回報的問題清單改寫題目（修正難度、Bloom、題型或補強知識依據），同時不偏離原 Intention",
        "topic": "qgen.refine",
        "task": "RefineQuestion",
        "params": [
            {"key": "question_id", "name": "題目 ID",
             "desc": "要 refine 的題目",
             "type": "string", "required": True, "example": "q_001"},
            {"key": "issues", "name": "問題清單",
             "desc": "Verifier 列出的不符項目",
             "type": "list[string]", "required": True,
             "example": ["bloom_too_low", "missing_grounding"]},
            {"key": "keep_intention", "name": "保留意圖",
             "desc": "改寫時是否強制保留原始 Intention",
             "type": "bool", "required": False, "example": True},
            {"key": "attempt_no", "name": "嘗試次數",
             "desc": "本次 refine 的嘗試序號",
             "type": "int", "required": True, "example": 2},
        ],
    },
]


# -----------------------------------------------------------------------------
# 工具
# -----------------------------------------------------------------------------
def _ensure_uuid(action: dict) -> dict:
    """若 id 為 corr-uuid 開頭，替換為真實 UUID。"""
    aid = action.get("id", "")
    if isinstance(aid, str) and aid.startswith("corr-uuid"):
        action = dict(action)
        action["id"] = str(uuid.uuid4())
    return action


def _serialize_example(val):
    """Neo4j 屬性只允許 primitive / array-of-primitive，
    將 dict 或含 dict 的 list 序列化為 JSON 字串以便存入。"""
    if val is None:
        return None
    if isinstance(val, dict):
        return json.dumps(val, ensure_ascii=False)
    if isinstance(val, list):
        if any(isinstance(x, (dict, list)) for x in val):
            return json.dumps(val, ensure_ascii=False)
        return val
    return val


def _ensure_database_exists(base_config: dict, db_name: str) -> None:
    """連 system database 嘗試 CREATE DATABASE（Community Edition 會跳過）。"""
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
                "  [0] Neo4j Community 僅支援單一 database。請改用 'neo4j' 或升級 Enterprise。",
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
    overrides = kg_cfg.get("neo4j_actions") or {}
    if not base:
        raise RuntimeError("Missing [kg.neo4j] config in gias.toml")
    if not overrides:
        raise RuntimeError("Missing [kg.neo4j_actions] config in gias.toml")
    merged = {**base, **overrides}
    return Neo4jBoltAdapter.from_config(merged, logger=None)


# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------
def main() -> int:
    cfg = get_agent_config()
    kg_cfg = cfg.get("kg", {})
    if kg_cfg.get("type") != "neo4j":
        print("錯誤：[kg].type 必須為 neo4j。", file=sys.stderr)
        return 1

    base = kg_cfg.get("neo4j") or {}
    overrides = kg_cfg.get("neo4j_actions") or {}
    if not base or not overrides:
        print("錯誤：缺少 [kg.neo4j] 或 [kg.neo4j_actions] 設定。", file=sys.stderr)
        return 1

    db_name = overrides.get("database", "actions")
    print("\n=== genexam_scenario.seed_actions ===")
    print(f"  目標 database : {db_name}")
    print(f"  連線         : {base.get('uri', '?')}")

    try:
        _ensure_database_exists({**base, **overrides}, db_name)
        time.sleep(1.0)
    except Exception as e:
        print(f"無法建立 / 連線 database：{e}", file=sys.stderr)
        return 1

    llm_cfg = cfg.get("llm")
    if not isinstance(llm_cfg, dict):
        print("錯誤：缺少 [llm] 設定（用於產生 embedding）。", file=sys.stderr)
        return 1
    llm = LLMClient.from_config(cfg)

    kg = _build_kg()
    try:
        kg.read("RETURN 1 AS ok", {})
        print("  [1] 連線 OK，開始清除舊資料")

        kg.write("MATCH (a:Action) DETACH DELETE a", {})
        kg.write("MATCH (p:Param) DETACH DELETE p", {})

        print(f"  [2] 寫入 {len(ACTIONS)} 個試題生成相關 action")
        dim: int | None = None

        for action in ACTIONS:
            action = _ensure_uuid(action)
            aid = action["id"]
            name = action["name"]
            desc = action["desc"]
            topic = action["topic"]
            task = action["task"]
            params = action.get("params", [])

            emb = llm.embed_text(desc)
            if not isinstance(emb, list) or not emb:
                raise RuntimeError(f"Invalid embedding for action '{task}'")
            if dim is None:
                dim = len(emb)
            elif len(emb) != dim:
                raise RuntimeError(
                    f"Embedding dim mismatch: expected {dim}, got {len(emb)} for '{task}'"
                )

            kg.write(
                """
                MERGE (a:Action {id:$id})
                SET a.name = $task,
                    a.display_name = $display_name,
                    a.description = $desc,
                    a.description_embedding = $emb,
                    a.topic = $topic,
                    a.task = $task,
                    a.version = $version
                """,
                {
                    "id": aid,
                    "display_name": name,
                    "desc": desc,
                    "emb": emb,
                    "topic": topic,
                    "task": task,
                    "version": "v1",
                },
            )

            for i, p in enumerate(params, start=1):
                kg.write(
                    """
                    MERGE (p:Param {key:$key})
                    SET p.name = $pname,
                        p.description = $pdesc,
                        p.type = $ptype,
                        p.required = $preq,
                        p.enum = $penum,
                        p.example = $pex
                    WITH p
                    MATCH (a:Action {id:$aid})
                    MERGE (a)-[r:HAS_PARAM]->(p)
                    SET r.required = $preq,
                        r.order = $order,
                        r.note = $note
                    """,
                    {
                        "key": p["key"],
                        "pname": p.get("name") or "",
                        "pdesc": p.get("desc") or "",
                        "ptype": p.get("type") or "string",
                        "preq": bool(p.get("required", False)),
                        "penum": p.get("enum"),
                        "pex": _serialize_example(p.get("example")),
                        "aid": aid,
                        "order": i,
                        "note": "",
                    },
                )

            print(
                f"    - {name} (task={task}, topic={topic}, "
                f"params={len(params)}, dim={len(emb)})"
            )

        if dim is None:
            raise RuntimeError("No actions seeded; embedding dim unknown.")

        print("  [3] 建立 / 確認 vector index (action_desc_vec)")
        kg.ensure_vector_index(
            index_name="action_desc_vec",
            label="Action",
            embedding_prop="description_embedding",
            dimensions=dim,
            similarity="cosine",
        )

        rows = kg.query(
            "MATCH (a:Action) RETURN count(a) AS n_actions", {},
        )
        n_actions = rows[0]["n_actions"] if rows else 0
        rows = kg.query(
            "MATCH (p:Param) RETURN count(p) AS n_params", {},
        )
        n_params = rows[0]["n_params"] if rows else 0
        print(f"  [✓] 完成。Actions={n_actions}, Params={n_params}\n")
        return 0
    finally:
        kg.close()


if __name__ == "__main__":
    raise SystemExit(main())

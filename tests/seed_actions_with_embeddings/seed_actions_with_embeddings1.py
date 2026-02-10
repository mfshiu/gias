# tests/seed_actions_with_embeddings.py

import os
from dotenv import load_dotenv

from src.app_helper import get_agent_config
from src.llm.client import LLMClient
from src.kg.adapter_neo4j import Neo4jBoltAdapter


ACTIONS = [
    {
        "name": "LocateExhibit",
        "desc": "引導訪客前往指定展區或攤位位置",
        "params": [
            {"key": "target_type", "name": "目標類型", "desc": "展區/攤位/展品", "type": "enum",
             "required": True, "enum": ["exhibit_zone", "booth", "exhibit"], "example": "booth"},
            {"key": "target_name", "name": "目標名稱", "desc": "目標的名稱或編號", "type": "string",
             "required": True, "example": "A12"},
            {"key": "current_location", "name": "目前位置", "desc": "訪客目前所在位置", "type": "string",
             "required": False, "example": "入口大廳"},
        ],
    },
    {
        "name": "ExplainExhibit",
        "desc": "介紹指定展品或展區的內容與特色",
        "params": [
            {"key": "target_type", "name": "目標類型", "desc": "展區/展品", "type": "enum",
             "required": True, "enum": ["exhibit_zone", "exhibit"], "example": "exhibit"},
            {"key": "target_name", "name": "目標名稱", "desc": "展品/展區名稱或編號", "type": "string",
             "required": True, "example": "智慧導覽眼鏡"},
            {"key": "language", "name": "語言", "desc": "說明使用的語言", "type": "string",
             "required": False, "example": "zh-TW"},
            {"key": "detail_level", "name": "詳盡程度", "desc": "簡短/一般/詳細", "type": "enum",
             "required": False, "enum": ["brief", "normal", "detailed"], "example": "normal"},
        ],
    },
    {
        "name": "SuggestRoute",
        "desc": "根據訪客位置與需求規劃最佳參觀路線",
        "params": [
            {"key": "current_location", "name": "目前位置", "desc": "訪客目前所在位置", "type": "string",
             "required": True, "example": "入口大廳"},
            {"key": "interests", "name": "興趣偏好", "desc": "訪客偏好的主題/關鍵字", "type": "list[string]",
             "required": False, "example": ["AI", "教育科技"]},
            {"key": "time_budget_min", "name": "可用時間(分鐘)", "desc": "預計參觀時間", "type": "int",
             "required": False, "example": 60},
            {"key": "avoid_crowd", "name": "避開人潮", "desc": "是否優先避開壅擠區", "type": "bool",
             "required": False, "example": True},
        ],
    },
    {
        "name": "AnswerFAQ",
        "desc": "回答關於會場規則、開放時間與服務設施的常見問題",
        "params": [
            {"key": "question", "name": "問題", "desc": "使用者提出的 FAQ 問題", "type": "string",
             "required": True, "example": "今天幾點閉館？"},
            {"key": "event_date", "name": "日期", "desc": "詢問所指的日期", "type": "string",
             "required": False, "example": "2026-02-06"},
            {"key": "language", "name": "語言", "desc": "回覆語言", "type": "string",
             "required": False, "example": "zh-TW"},
        ],
    },
    {
        "name": "LocateFacility",
        "desc": "協助查找洗手間、出口、服務台或無障礙設施",
        "params": [
            {"key": "facility_type", "name": "設施類型", "desc": "要找的設施種類", "type": "enum",
             "required": True, "enum": ["restroom", "exit", "service_desk", "accessible"], "example": "restroom"},
            {"key": "current_location", "name": "目前位置", "desc": "訪客目前所在位置", "type": "string",
             "required": False, "example": "B館中庭"},
        ],
    },
    {
        "name": "ProvideSchedule",
        "desc": "提供活動、演講或表演的時間與地點資訊",
        "params": [
            {"key": "date", "name": "日期", "desc": "要查詢的日期", "type": "string",
             "required": False, "example": "2026-02-06"},
            {"key": "topic", "name": "主題/關鍵字", "desc": "活動主題關鍵字", "type": "string",
             "required": False, "example": "LLM"},
            {"key": "venue", "name": "場地", "desc": "指定館別/舞台/會議室", "type": "string",
             "required": False, "example": "主舞台"},
        ],
    },
    {
        "name": "RecommendExhibits",
        "desc": "根據訪客興趣推薦適合的展區或活動",
        "params": [
            {"key": "interests", "name": "興趣偏好", "desc": "偏好主題/關鍵字", "type": "list[string]",
             "required": True, "example": ["智慧製造", "ESG"]},
            {"key": "current_location", "name": "目前位置", "desc": "訪客目前所在位置", "type": "string",
             "required": False, "example": "A館入口"},
            {"key": "limit", "name": "推薦數量", "desc": "最多推薦幾個", "type": "int",
             "required": False, "example": 5},
        ],
    },
    {
        "name": "CrowdStatus",
        "desc": "說明各展區目前的人潮與擁擠狀況",
        "params": [
            {"key": "target_area", "name": "區域", "desc": "要查詢人潮的展區/館別", "type": "string",
             "required": False, "example": "B館"},
            {"key": "time_window_min", "name": "時間窗(分鐘)", "desc": "近幾分鐘的統計", "type": "int",
             "required": False, "example": 15},
        ],
    },
    {
        "name": "NavigationAssistance",
        "desc": "在移動過程中即時提供方向與轉彎提示",
        "params": [
            {"key": "destination", "name": "目的地", "desc": "要前往的目標名稱/編號", "type": "string",
             "required": True, "example": "A12"},
            {"key": "current_location", "name": "目前位置", "desc": "目前所在位置", "type": "string",
             "required": True, "example": "服務台"},
            {"key": "mode", "name": "移動方式", "desc": "步行/無障礙", "type": "enum",
             "required": False, "enum": ["walk", "accessible"], "example": "walk"},
        ],
    },
    {
        "name": "ExplainDirections",
        "desc": "以自然語言解釋如何從目前位置前往目的地",
        "params": [
            {"key": "destination", "name": "目的地", "desc": "目標名稱/編號", "type": "string",
             "required": True, "example": "主舞台"},
            {"key": "current_location", "name": "目前位置", "desc": "出發點", "type": "string",
             "required": False, "example": "入口大廳"},
            {"key": "landmarks", "name": "地標偏好", "desc": "是否用地標輔助描述", "type": "bool",
             "required": False, "example": True},
        ],
    },
]


def main():
    load_dotenv()

    if not (os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY")):
        raise RuntimeError("Missing OPENAI_API_KEY / OPENAI_KEY")

    cfg = get_agent_config()
    kg_cfg = cfg.get("kg", {})
    if kg_cfg.get("type") != "neo4j":
        raise RuntimeError("KG type must be neo4j for this seed script")

    kg = Neo4jBoltAdapter.from_config(kg_cfg["neo4j"], logger=None)
    llm = LLMClient.from_env()

    print(">>> Clearing existing Action nodes (and their HAS_PARAM rels)")
    kg.write("MATCH (a:Action) DETACH DELETE a")

    # 你要更乾淨：連 Param 也清（如果你希望 Param 是全域字典且可累積，可把這段拿掉）
    print(">>> Clearing existing Param nodes")
    kg.write("MATCH (p:Param) DETACH DELETE p")

    print(">>> Seeding Params + Actions + Relationships")

    dim: int | None = None

    for action in ACTIONS:
        name = action["name"]
        desc = action["desc"]
        params = action.get("params", [])

        emb = llm.embed_text(desc)
        if not isinstance(emb, list) or not emb:
            raise RuntimeError(f"Invalid embedding returned for action '{name}'")

        if dim is None:
            dim = len(emb)
        elif len(emb) != dim:
            raise RuntimeError(f"Embedding dimension mismatch: expected {dim}, got {len(emb)} for '{name}'")

        # 1) 建 Action
        kg.write(
            """
            MERGE (a:Action {name:$name})
            SET a.description = $desc,
                a.description_embedding = $emb
            """,
            {"name": name, "desc": desc, "emb": emb},
        )

        # 2) 建 Param（可共用）：用 key 做 MERGE
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
                MATCH (a:Action {name:$aname})
                MERGE (a)-[r:HAS_PARAM]->(p)
                SET r.required = $preq,
                    r.order = $order,
                    r.note = $note
                """,
                {
                    "key": p["key"],
                    "pname": p.get("name"),
                    "pdesc": p.get("desc"),
                    "ptype": p.get("type"),
                    "preq": bool(p.get("required", False)),
                    "penum": p.get("enum"),
                    "pex": p.get("example"),
                    "aname": name,
                    "order": i,
                    "note": "",
                },
            )

        print(f"  - Seeded Action: {name} (params={len(params)}, dim={len(emb)})")

    if dim is None:
        raise RuntimeError("No actions seeded; embedding dimension is unknown.")

    print(">>> Ensuring vector index (action_desc_vec)")
    kg.ensure_vector_index(
        index_name="action_desc_vec",
        label="Action",
        embedding_prop="description_embedding",
        dimensions=dim,
        similarity="cosine",
    )

    # （選配）Param 也想做向量檢索：可加 p.description_embedding，並建立 param_desc_vec
    # 但要額外 embed_text(p.description)；你若要我也一起加，我可以再給你版本。

    print(">>> Done.")
    kg.close()


if __name__ == "__main__":
    main()

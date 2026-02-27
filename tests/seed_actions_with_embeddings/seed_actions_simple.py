# tests/seed_actions_with_embeddings/seed_actions_simple.py
# 參考 seed_actions_and_agents.py，移除 Agent 相關資料，改用簡化 action 格式。
#
# 執行：python -m tests.seed_actions_with_embeddings.seed_actions_simple
#
# --- 查詢（embedding 存在 Action.description_embedding，之後只用 Table 查詢）---
#
# 建議使用 Table 檢視（執行前先點 Table 圖示，不要用 Graph），避免載入大屬性：
#
#   MATCH (a:Action)-[r:HAS_PARAM]->(p:Param)
#   RETURN a.id, a.name, a.task, a.topic, p.key, p.name
#   ORDER BY a.task, r.order LIMIT 50

import json
import uuid

from src.app_helper import get_agent_config
from src.llm.client import LLMClient
from src.kg.adapter_neo4j import Neo4jBoltAdapter


# 新格式：id, name, desc, topic, task, params（無 Agent）
ACTIONS = [
    {
        "id": "corr-uuid-locate",
        "name": "Locate Exhibit",
        "desc": "引導訪客前往指定展區或攤位位置",
        "topic": "navigation.request",
        "task": "LocateExhibit",
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
        "id": "corr-uuid-explain-exhibit",
        "name": "Explain Exhibit",
        "desc": "介紹指定展品或展區的內容與特色",
        "topic": "info.request",
        "task": "ExplainExhibit",
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
        "id": "corr-uuid-suggest-route",
        "name": "Suggest Route",
        "desc": "根據訪客位置與需求規劃最佳參觀路線",
        "topic": "navigation.request",
        "task": "SuggestRoute",
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
        "id": "corr-uuid-answer-faq",
        "name": "Answer FAQ",
        "desc": "回答關於會場規則、開放時間與服務設施的常見問題",
        "topic": "info.request",
        "task": "AnswerFAQ",
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
        "id": "corr-uuid-locate-facility",
        "name": "Locate Facility",
        "desc": "協助查找洗手間、出口、服務台或無障礙設施",
        "topic": "info.request",
        "task": "LocateFacility",
        "params": [
            {"key": "facility_type", "name": "設施類型", "desc": "要找的設施種類", "type": "enum",
             "required": True, "enum": ["restroom", "exit", "service_desk", "accessible"], "example": "restroom"},
            {"key": "current_location", "name": "目前位置", "desc": "訪客目前所在位置", "type": "string",
             "required": False, "example": "B館中庭"},
        ],
    },
    {
        "id": "corr-uuid-provide-schedule",
        "name": "Provide Schedule",
        "desc": "提供活動、演講或表演的時間與地點資訊",
        "topic": "info.request",
        "task": "ProvideSchedule",
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
        "id": "corr-uuid-recommend-exhibits",
        "name": "Recommend Exhibits",
        "desc": "根據訪客興趣推薦適合的展區或活動",
        "topic": "info.request",
        "task": "RecommendExhibits",
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
        "id": "corr-uuid-crowd-status",
        "name": "Crowd Status",
        "desc": "說明各展區目前的人潮與擁擠狀況",
        "topic": "info.request",
        "task": "CrowdStatus",
        "params": [
            {"key": "target_area", "name": "區域", "desc": "要查詢人潮的展區/館別", "type": "string",
             "required": False, "example": "B館"},
            {"key": "time_window_min", "name": "時間窗(分鐘)", "desc": "近幾分鐘的統計", "type": "int",
             "required": False, "example": 15},
        ],
    },
    {
        "id": "corr-uuid-navigation-assistance",
        "name": "Navigation Assistance",
        "desc": "在移動過程中即時提供方向與轉彎提示",
        "topic": "navigation.request",
        "task": "NavigationAssistance",
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
        "id": "corr-uuid-explain-directions",
        "name": "Explain Directions",
        "desc": "以自然語言解釋如何從目前位置前往目的地",
        "topic": "navigation.request",
        "task": "ExplainDirections",
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


def _ensure_uuid(a: dict) -> dict:
    """若 id 為 corr-uuid 開頭，替換為真實 UUID。"""
    aid = a.get("id", "")
    if isinstance(aid, str) and aid.startswith("corr-uuid"):
        a = dict(a)
        a["id"] = str(uuid.uuid4())
    return a


def main():
    cfg = get_agent_config()

    kg_cfg = cfg.get("kg", {})
    if kg_cfg.get("type") != "neo4j":
        raise RuntimeError("KG type must be neo4j for this seed script")

    neo = kg_cfg.get("neo4j")
    if not isinstance(neo, dict):
        raise RuntimeError("Missing [kg.neo4j] config in gias.toml")

    kg = Neo4jBoltAdapter.from_config(neo, logger=None)

    llm_cfg = cfg.get("llm")
    if not isinstance(llm_cfg, dict):
        raise RuntimeError("Missing [llm] config in gias.toml")
    llm = LLMClient.from_config(cfg)

    # 清資料：Action/Param（embedding 存在 Action.description_embedding）
    print(">>> Clearing Action/Param nodes")
    kg.write("MATCH (a:Action) DETACH DELETE a")
    kg.write("MATCH (p:Param) DETACH DELETE p")

    print(">>> Seeding Actions (embedding in Action.description_embedding)")

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
            raise RuntimeError(f"Invalid embedding returned for action '{task}'")

        if dim is None:
            dim = len(emb)
        elif len(emb) != dim:
            raise RuntimeError(f"Embedding dimension mismatch: expected {dim}, got {len(emb)} for '{task}'")

        # Action 節點（含 description_embedding，供 vector search；之後只用 Table 查詢）
        kg.write(
            """
            MERGE (a:Action {id:$id})
            SET a.name = $task,
                a.display_name = $name,
                a.description = $desc,
                a.description_embedding = $emb,
                a.topic = $topic,
                a.task = $task,
                a.version = $version
            """,
            {
                "id": aid,
                "name": name,
                "desc": desc,
                "emb": emb,
                "topic": topic,
                "task": task,
                "version": "v1",
            },
        )

        for i, p in enumerate(params, start=1):
            # 避免 null 造成 Neo4j Browser 的 replace() 崩潰
            pname = p.get("name") or ""
            pdesc = p.get("desc") or ""
            ptype = p.get("type") or "string"
            penum = p.get("enum")
            pex = p.get("example")
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
                    "pname": pname,
                    "pdesc": pdesc,
                    "ptype": ptype,
                    "preq": bool(p.get("required", False)),
                    "penum": penum,
                    "pex": pex,
                    "aid": aid,
                    "order": i,
                    "note": "",
                },
            )

        print(f"  - Seeded Action: {name} (task={task}, topic={topic}, params={len(params)}, dim={len(emb)})")

    if dim is None:
        raise RuntimeError("No actions seeded; embedding dimension is unknown.")

    print(">>> Ensuring vector index (action_desc_vec on Action.description_embedding)")
    kg.ensure_vector_index(
        index_name="action_desc_vec",
        label="Action",
        embedding_prop="description_embedding",
        dimensions=dim,
        similarity="cosine",
    )

    print(">>> Done.")
    kg.close()


def export_json(out_path: str = "actions_simple.json"):
    """匯出 ACTIONS 為 JSON 檔（不寫入 Neo4j）。"""
    data = [_ensure_uuid(dict(a)) for a in ACTIONS]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Exported {len(data)} actions to {out_path}")


if __name__ == "__main__":
    main()

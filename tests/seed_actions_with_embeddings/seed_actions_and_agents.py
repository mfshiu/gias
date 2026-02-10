# tests/seed_actions_with_embeddings_agent.py
import os
import json
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

# === 新增：常駐代理定義（可依你的實際部署拆更細）===
AGENTS = [
    {
        "id": "nav-agent",
        "name": "NavigationAgent",
        "desc": "負責定位、導引、路線規劃、即時導航提示",
        "status": "active",
        "version": "1.0.0",
    },
    {
        "id": "info-agent",
        "name": "InfoAgent",
        "desc": "負責展品介紹、FAQ、活動時程、推薦與人潮資訊",
        "status": "active",
        "version": "1.0.0",
    },
]

# Action -> Agent + PubSub 契約（topic / schema / timeout 等）
# topic 命名規則：gias.expo.<domain>.<action>.<req|resp>.v1
ACTION_CONTRACTS = {
    "LocateExhibit":         {"agent_id": "nav-agent",  "domain": "nav",  "timeout_ms": 8000},
    "SuggestRoute":          {"agent_id": "nav-agent",  "domain": "nav",  "timeout_ms": 12000},
    "NavigationAssistance":  {"agent_id": "nav-agent",  "domain": "nav",  "timeout_ms": 15000},
    "ExplainDirections":     {"agent_id": "nav-agent",  "domain": "nav",  "timeout_ms": 8000},

    "ExplainExhibit":        {"agent_id": "info-agent", "domain": "info", "timeout_ms": 12000},
    "AnswerFAQ":             {"agent_id": "info-agent", "domain": "info", "timeout_ms": 8000},
    "LocateFacility":        {"agent_id": "info-agent", "domain": "info", "timeout_ms": 8000},
    "ProvideSchedule":       {"agent_id": "info-agent", "domain": "info", "timeout_ms": 8000},
    "RecommendExhibits":     {"agent_id": "info-agent", "domain": "info", "timeout_ms": 12000},
    "CrowdStatus":           {"agent_id": "info-agent", "domain": "info", "timeout_ms": 6000},
}


def _topic(domain: str, action: str, kind: str, version: str = "v1") -> str:
    # kind: "req" | "resp"
    return f"gias.expo.{domain}.{action}.{kind}.{version}"


def _default_headers() -> list[str]:
    # 讓 client/dispatcher 能一致處理 correlation/reply-to/trace/語系等
    return ["correlation_id", "reply_to", "trace_id", "tenant_id", "lang"]


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

    # === 清資料：Action/Param/Agent/Topic/Schema 都清掉，避免殘留舊契約 ===
    print(">>> Clearing existing nodes: Action/Param/Agent/Topic/MessageSchema")
    kg.write("MATCH (a:Action) DETACH DELETE a")
    kg.write("MATCH (p:Param) DETACH DELETE p")
    kg.write("MATCH (ag:Agent) DETACH DELETE ag")
    kg.write("MATCH (t:Topic) DETACH DELETE t")
    kg.write("MATCH (s:MessageSchema) DETACH DELETE s")

    # === 先種 Agent 節點 ===
    print(">>> Seeding Agents")
    for ag in AGENTS:
        kg.write(
            """
            MERGE (ag:Agent {id:$id})
            SET ag.name=$name,
                ag.description=$desc,
                ag.status=$status,
                ag.version=$version
            """,
            ag,
        )
        print(f"  - Seeded Agent: {ag['id']} ({ag['name']})")

    print(">>> Seeding Params + Actions + PubSub Contracts")

    dim: int | None = None

    for action in ACTIONS:
        name = action["name"]
        desc = action["desc"]
        params = action.get("params", [])

        contract = ACTION_CONTRACTS.get(name)
        if not contract:
            raise RuntimeError(f"Missing ACTION_CONTRACTS for action '{name}'")

        domain = contract["domain"]
        agent_id = contract["agent_id"]
        timeout_ms = int(contract.get("timeout_ms", 8000))

        request_topic = _topic(domain, name, "req", "v1")
        response_topic = _topic(domain, name, "resp", "v1")

        # message schema：把 params + headers 固化成「可溝通契約」
        req_schema_name = f"{name}Request.v1"
        resp_schema_name = f"{name}Response.v1"

        req_example = {
            "action": name,
            "args": {p["key"]: p.get("example") for p in params},
            "meta": {"timestamp": "2026-02-08T00:00:00+08:00"},
        }
        resp_example = {
            "action": name,
            "ok": True,
            "result": {},
            "error": None,
        }

        emb = llm.embed_text(desc)
        if not isinstance(emb, list) or not emb:
            raise RuntimeError(f"Invalid embedding returned for action '{name}'")

        if dim is None:
            dim = len(emb)
        elif len(emb) != dim:
            raise RuntimeError(f"Embedding dimension mismatch: expected {dim}, got {len(emb)} for '{name}'")

        # 1) Action（加上 timeout / retries / version / idempotent 等可調度資訊）
        kg.write(
            """
            MERGE (a:Action {name:$name})
            SET a.description = $desc,
                a.description_embedding = $emb,
                a.timeout_ms = $timeout_ms,
                a.retries = $retries,
                a.idempotent = $idempotent,
                a.version = $version
            WITH a
            MATCH (ag:Agent {id:$agent_id})
            MERGE (ag)-[:IMPLEMENTS]->(a)
            """,
            {
                "name": name,
                "desc": desc,
                "emb": emb,
                "timeout_ms": timeout_ms,
                "retries": 1,
                "idempotent": False,
                "version": "v1",
                "agent_id": agent_id,
            },
        )

        # 2) Topic + Schema（request/response）
        kg.write(
            """
            MERGE (t:Topic {name:$tname})
            SET t.transport=$transport,
                t.scope=$scope,
                t.version=$tversion,
                t.pattern=$pattern
            """,
            {
                "tname": request_topic,
                "transport": "pubsub",
                "scope": "expo",
                "tversion": "v1",
                "pattern": request_topic,
            },
        )
        kg.write(
            """
            MERGE (t:Topic {name:$tname})
            SET t.transport=$transport,
                t.scope=$scope,
                t.version=$tversion,
                t.pattern=$pattern
            """,
            {
                "tname": response_topic,
                "transport": "pubsub",
                "scope": "expo",
                "tversion": "v1",
                "pattern": response_topic,
            },
        )

        kg.write(
            """
            MERGE (s:MessageSchema {name:$sname})
            SET s.content_type=$content_type,
                s.required_headers=$headers,
                s.example_json=$example_json,
                s.version=$sversion
            WITH s
            MATCH (t:Topic {name:$tname})
            MERGE (t)-[:HAS_SCHEMA]->(s)
            """,
            {
                "sname": req_schema_name,
                "content_type": "application/json",
                "headers": _default_headers(),
                "example_json": json.dumps(req_example, ensure_ascii=False),
                "sversion": "v1",
                "tname": request_topic,
            },
        )

        kg.write(
            """
            MERGE (s:MessageSchema {name:$sname})
            SET s.content_type=$content_type,
                s.required_headers=$headers,
                s.example_json=$example_json,
                s.version=$sversion
            WITH s
            MATCH (t:Topic {name:$tname})
            MERGE (t)-[:HAS_SCHEMA]->(s)
            """,
            {
                "sname": resp_schema_name,
                "content_type": "application/json",
                "headers": _default_headers(),
                "example_json": json.dumps(resp_example, ensure_ascii=False),
                "sversion": "v1",
                "tname": response_topic,
            },
        )

        # 3) Action -> Topic 關係（這就是 client 查到就能發佈的關鍵）
        kg.write(
            """
            MATCH (a:Action {name:$aname})
            MATCH (treq:Topic {name:$req})
            MATCH (tresp:Topic {name:$resp})
            MERGE (a)-[r1:REQUESTS]->(treq)
            SET r1.method="pub",
                r1.mode="request",
                r1.timeout_ms=$timeout_ms
            MERGE (a)-[r2:RESPONDS]->(tresp)
            SET r2.method="pub",
                r2.mode="response"
            """,
            {"aname": name, "req": request_topic, "resp": response_topic, "timeout_ms": timeout_ms},
        )

        # 4) Param（維持你原本的做法）
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

        print(
            f"  - Seeded Action: {name} -> agent={agent_id}, "
            f"req={request_topic}, resp={response_topic}, params={len(params)}, dim={len(emb)}"
        )

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

    print(">>> Done.")
    kg.close()


if __name__ == "__main__":
    main()

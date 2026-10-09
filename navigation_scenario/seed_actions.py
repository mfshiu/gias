"""
Navigation Scenario：Action KG 種子程式

將 7 個導航相關 actions（含 embedding）寫入 [kg.neo4j_actions] 指向的
Neo4j database（預設為 'actions'）。

包含 action：
  LocateExhibit, NavigationAssistance, SuggestRoute, ExplainDirections,
  CrowdStatus, LocateFacility, ReplanRoute

執行：
    python -m navigation_scenario.seed_actions

注意：本 seed 為「破壞性」操作，會清空 actions database 中既有的
      Action / Param 節點再重新寫入。
"""

from __future__ import annotations

import sys
import time
import uuid

from src.app_helper import get_agent_config
from src.kg.adapter_neo4j import Neo4jBoltAdapter
from src.llm.client import LLMClient


# -----------------------------------------------------------------------------
# 導航專用 Action 定義
# -----------------------------------------------------------------------------
ACTIONS: list[dict] = [
    {
        "id": "corr-uuid-locate-exhibit",
        "name": "Locate Exhibit",
        "desc": "引導訪客前往指定的展區、攤位或展品位置",
        "topic": "navigation.request",
        "task": "LocateExhibit",
        "params": [
            {"key": "target_type", "name": "目標類型",
             "desc": "展區/攤位/展品",
             "type": "enum", "required": True,
             "enum": ["exhibit_zone", "booth", "exhibit"],
             "example": "exhibit_zone"},
            {"key": "target_name", "name": "目標名稱",
             "desc": "目標的名稱或編號",
             "type": "string", "required": True, "example": "AI_Tech_Area"},
            {"key": "current_location", "name": "目前位置",
             "desc": "訪客目前所在位置",
             "type": "string", "required": False, "example": "P_Entrance"},
        ],
    },
    {
        "id": "corr-uuid-navigation-assistance",
        "name": "Navigation Assistance",
        "desc": "在移動過程中即時提供方向、轉彎與抵達提示",
        "topic": "navigation.request",
        "task": "NavigationAssistance",
        "params": [
            {"key": "destination", "name": "目的地",
             "desc": "要前往的目標名稱/編號",
             "type": "string", "required": True, "example": "B_AI1"},
            {"key": "current_location", "name": "目前位置",
             "desc": "目前所在位置",
             "type": "string", "required": True, "example": "P_North_Hub"},
            {"key": "mode", "name": "移動方式",
             "desc": "步行/無障礙",
             "type": "enum", "required": False,
             "enum": ["walk", "accessible"], "example": "walk"},
        ],
    },
    {
        "id": "corr-uuid-suggest-route",
        "name": "Suggest Route",
        "desc": "根據訪客位置、目標與限制（避開擁擠/封閉/特定區域）規劃最佳路線",
        "topic": "navigation.request",
        "task": "SuggestRoute",
        "params": [
            {"key": "current_location", "name": "目前位置",
             "desc": "出發點", "type": "string",
             "required": True, "example": "P_Entrance"},
            {"key": "destination", "name": "目的地",
             "desc": "目標展區或攤位",
             "type": "string", "required": True, "example": "AI_Tech_Area"},
            {"key": "avoid_crowded", "name": "避開擁擠",
             "desc": "是否避開人潮壅塞區域",
             "type": "bool", "required": False, "example": True},
            {"key": "avoid_closed", "name": "避開封閉區",
             "desc": "是否強制避開狀態為 Closed 的區域",
             "type": "bool", "required": False, "example": True},
            {"key": "waypoints", "name": "途經點",
             "desc": "多步驟導航的中間目標（依順序）",
             "type": "list[string]", "required": False,
             "example": ["Robotics_Area", "VR_Area"]},
        ],
    },
    {
        "id": "corr-uuid-explain-directions",
        "name": "Explain Directions",
        "desc": "以自然語言解釋如何從目前位置前往目的地（含地標說明）",
        "topic": "navigation.request",
        "task": "ExplainDirections",
        "params": [
            {"key": "destination", "name": "目的地",
             "desc": "目標名稱/編號",
             "type": "string", "required": True, "example": "VR_Area"},
            {"key": "current_location", "name": "目前位置",
             "desc": "出發點",
             "type": "string", "required": False, "example": "P_Info"},
            {"key": "landmarks", "name": "地標偏好",
             "desc": "是否用地標輔助描述",
             "type": "bool", "required": False, "example": True},
        ],
    },
    {
        "id": "corr-uuid-crowd-status",
        "name": "Crowd Status",
        "desc": "回報指定展區或全場目前的人潮與壅塞狀況",
        "topic": "info.request",
        "task": "CrowdStatus",
        "params": [
            {"key": "target_area", "name": "區域",
             "desc": "要查詢人潮的展區",
             "type": "string", "required": False, "example": "AI_Tech_Area"},
            {"key": "time_window_min", "name": "時間窗(分鐘)",
             "desc": "近幾分鐘的統計",
             "type": "int", "required": False, "example": 10},
        ],
    },
    {
        "id": "corr-uuid-locate-facility",
        "name": "Locate Facility",
        "desc": "協助查找洗手間、出口、服務台或無障礙設施",
        "topic": "navigation.request",
        "task": "LocateFacility",
        "params": [
            {"key": "facility_type", "name": "設施類型",
             "desc": "要找的設施種類",
             "type": "enum", "required": True,
             "enum": ["restroom", "exit", "service_desk", "accessible"],
             "example": "restroom"},
            {"key": "current_location", "name": "目前位置",
             "desc": "訪客目前所在位置",
             "type": "string", "required": False, "example": "P_North_Hub"},
        ],
    },
    {
        "id": "corr-uuid-replan-route",
        "name": "Replan Route",
        "desc": "因為人潮壅塞、區域關閉或改道事件，從目前位置重新規劃前往剩餘目標的路線",
        "topic": "navigation.request",
        "task": "ReplanRoute",
        "params": [
            {"key": "current_location", "name": "目前位置",
             "desc": "重新規劃時的所在位置",
             "type": "string", "required": True, "example": "B_RB1"},
            {"key": "remaining_goals", "name": "剩餘目標",
             "desc": "尚未達成的目標 zone 順序",
             "type": "list[string]", "required": True,
             "example": ["VR_Area"]},
            {"key": "reason", "name": "重規劃原因",
             "desc": "觸發 replan 的事件種類",
             "type": "enum", "required": False,
             "enum": ["crowd_congestion", "area_closure", "route_detour"],
             "example": "area_closure"},
            {"key": "blocked_nodes", "name": "封鎖節點",
             "desc": "本次需避開的節點/邊識別",
             "type": "list[string]", "required": False,
             "example": ["B_RB2"]},
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
    print("\n=== navigation_scenario.seed_actions ===")
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

        print(f"  [2] 寫入 {len(ACTIONS)} 個導航相關 action")
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
                        "pex": p.get("example"),
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

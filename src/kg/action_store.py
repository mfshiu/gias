# src/kg/action_store.py
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from .cache import TTLCache

logger = logging.getLogger(__name__)

ACTION_DESC_INDEX = "action_desc_vec"

_PARAM_COLUMNS = """
            p.key AS key,
            p.name AS name,
            coalesce(p.desc, p.description) AS desc,
            p.type AS type,
            coalesce(r.required, p.required, false) AS required,
            p.enum AS enum,
            p.example AS example,
            coalesce(r.order, 999) AS order"""


class ActionStore:
    def __init__(self, kg_adapter, *, cache_ttl_sec: float = 300.0):
        """cache_ttl_sec：Action 的參數、輸出、前置條件的快取時效（秒）；≤ 0 表示每次都查 KG。"""
        self.kg = kg_adapter
        self._cache = TTLCache(cache_ttl_sec)
        # 已確認可用的向量索引維度；比對時不再重複檢查（見 ensure_action_desc_index）
        self._index_ready_dim: int | None = None

    def clear_cache(self) -> None:
        """重新 seed Action KG 後可呼叫，立即讀到新資料。"""
        self._cache.clear()

    def _cached(self, kind: str, action_name: str, load):
        hit, value = self._cache.get((kind, action_name))
        if hit:
            return value
        value = load()
        self._cache.put((kind, action_name), value)
        return value

    # ---------------------------
    # Index utilities (Neo4j 5.x)
    # ---------------------------
    def _show_index(self, name: str) -> dict | None:
        """
        回傳單一 index 的資訊（Neo4j 5.x: SHOW INDEXES）。
        """
        rows = self.kg.query(
            """
            SHOW INDEXES
            YIELD name, type, entityType, labelsOrTypes, properties, options, state
            WHERE name = $n
            RETURN name, type, entityType, labelsOrTypes, properties, options, state
            """,
            {"n": name},
        )
        return rows[0] if rows else None

    def _extract_vector_dimensions(self, idx_row: dict) -> int | None:
        """
        從 SHOW INDEXES 的 options 中嘗試抓 vector.dimensions。
        容錯處理：不同 driver/版本 options 結構可能不同。
        """
        if not idx_row:
            return None

        options = idx_row.get("options") or {}
        if isinstance(options, str):
            # 有些 driver 可能把 map 轉成字串，這裡先不解析，回 None 走重建策略
            return None

        index_config = options.get("indexConfig") or options.get("index_config") or {}
        if not isinstance(index_config, dict):
            return None

        # 常見 key
        for k in ("vector.dimensions", "`vector.dimensions`", "vector.dimensions`", "`vector.dimensions"):
            if k in index_config:
                try:
                    return int(index_config[k])
                except Exception:
                    return None

        # 再掃一次所有 key
        for key, val in index_config.items():
            if "vector.dimensions" in str(key):
                try:
                    return int(val)
                except Exception:
                    return None

        return None

    def _await_index_online(self, name: str) -> None:
        """
        ✅ 等待 index ONLINE，避免剛建好就查（常見回 0 matches 的主因）。
        """
        try:
            self.kg.query("CALL db.awaitIndex($n)", {"n": name})
            return
        except Exception:
            # fallback：輪詢 state
            for _ in range(30):
                row = self._show_index(name)
                state = (row or {}).get("state")
                if str(state).upper() == "ONLINE":
                    return
                time.sleep(0.2)

    def ensure_action_desc_index(self, dimensions: int) -> None:
        """
        確認 action_desc_vec 可以查詢（比對動作前呼叫）。

        同一個維度只在第一次（或上次向量查詢失敗後）檢查，之後不再查詢 KG：
        - 不存在：建立（IF NOT EXISTS）並等待 ONLINE
        - 存在但還不是 ONLINE：等待 ONLINE
        - 維度與查詢向量不同：記錄錯誤，不刪除。索引裡的向量是 seed 時算的，重建索引不會改變它們；
          換了 embedding 模型時請重新執行 seed（seed 會重建索引）
        - 讀不到維度：視為可用
        比對時不會刪除或重建索引。
        """
        dimensions = int(dimensions)
        if self._index_ready_dim == dimensions:
            return
        name = ACTION_DESC_INDEX
        idx = self._show_index(name)

        if idx:
            existing_dim = self._extract_vector_dimensions(idx)
            if existing_dim is not None and int(existing_dim) != dimensions:
                logger.error(
                    "Vector index %s has %s dimensions but query embeddings have %s; "
                    "re-run the action seed to rebuild the embeddings and the index.",
                    name, existing_dim, dimensions,
                )
            if str(idx.get("state") or "").upper() != "ONLINE":
                self._await_index_online(name)
            self._index_ready_dim = dimensions
            return

        # 不存在：建立
        # ✅ 加 IF NOT EXISTS，避免重跑測試噴錯；CREATE INDEX 為寫入操作
        self.kg.query(
            f"""
            CREATE VECTOR INDEX {name} IF NOT EXISTS
            FOR (a:Action) ON (a.description_embedding)
            OPTIONS {{
              indexConfig: {{
                `vector.dimensions`: $d,
                `vector.similarity_function`: 'cosine'
              }}
            }}
            """,
            {"d": int(dimensions)},
            write=True,
        )

        # ✅ 最關鍵：等 ONLINE
        self._await_index_online(name)
        self._index_ready_dim = dimensions

    # ---------------------------
    # Params schema
    # ---------------------------
    def get_action_params(self, action_name: str) -> list[dict]:
        """取得 Action 的 params schema，相容 seed_actions（p.desc）與 seed_actions_simple（p.description）。"""
        cypher = f"""
        MATCH (a:Action {{name:$name}})-[r:HAS_PARAM]->(p:Param)
        RETURN {_PARAM_COLUMNS}
        ORDER BY coalesce(r.required, p.required, false) DESC, coalesce(r.order, 999), p.key ASC
        """
        return self._cached("params", action_name, lambda: self.kg.query(cypher, {"name": action_name}) or [])

    def get_actions_schema(self, action_names: list[str]) -> dict[str, dict[str, list[dict]]]:
        """多個 Action 的參數與輸出：{name: {"params": [...], "outputs": [...]}}。

        沒有快取的 Action 以兩個批次查詢取得（不再每個 Action 各查一次），結果放進快取。
        """
        names = list(dict.fromkeys(n for n in action_names if n))
        result: dict[str, dict[str, list[dict]]] = {n: {} for n in names}
        missing: dict[str, list[str]] = {"params": [], "outputs": []}
        for name in names:
            for kind in missing:
                hit, value = self._cache.get((kind, name))
                if hit:
                    result[name][kind] = value
                else:
                    missing[kind].append(name)
        if missing["params"]:
            rows = self.kg.query(
                f"""
                UNWIND $names AS action
                MATCH (a:Action {{name: action}})-[r:HAS_PARAM]->(p:Param)
                RETURN action, {_PARAM_COLUMNS}
                ORDER BY action, coalesce(r.required, p.required, false) DESC, coalesce(r.order, 999), p.key ASC
                """,
                {"names": missing["params"]},
            ) or []
            self._fill(result, "params", missing["params"], rows)
        if missing["outputs"]:
            rows = self.kg.query(
                """
                UNWIND $names AS action
                MATCH (a:Action {name: action})-[r:HAS_OUTPUT]->(o:Output)
                RETURN action, o.key AS key, o.name AS name, o.desc AS desc, o.type AS type
                ORDER BY action, coalesce(r.order, 999), o.key
                """,
                {"names": missing["outputs"]},
            ) or []
            self._fill(result, "outputs", missing["outputs"], rows)
        return result

    def _fill(self, result: dict, kind: str, names: list[str], rows: list[dict]) -> None:
        grouped: dict[str, list[dict]] = {n: [] for n in names}
        for row in rows:
            row = dict(row)
            action = row.pop("action", None)
            if action in grouped:
                grouped[action].append(row)
        for name, items in grouped.items():
            result[name][kind] = items
            self._cache.put((kind, name), items)

    # ---------------------------
    # Preconditions / effects（見 src/core/intent/preconditions.py）
    # ---------------------------
    def get_action_conditions(self, action_name: str) -> dict[str, list[dict]]:
        """取得 Action 的前置條件與效果：{"preconditions": [...], "effects": [...]}。"""
        return self._cached("conditions", action_name, lambda: self._load_conditions(action_name))

    def _load_conditions(self, action_name: str) -> dict[str, list[dict]]:
        rows = self.kg.query(
            """
            MATCH (a:Action {name:$name})
            OPTIONAL MATCH (a)-[:REQUIRES]->(p:Precondition)
            WITH a, collect(properties(p)) AS pre
            OPTIONAL MATCH (a)-[:PRODUCES]->(e:Effect)
            RETURN pre AS preconditions, collect(properties(e)) AS effects
            """,
            {"name": action_name},
        )
        row = rows[0] if rows else {}
        return {"preconditions": list(row.get("preconditions") or []), "effects": list(row.get("effects") or [])}

    # ---------------------------
    # Outputs（見 src/core/intent/dataflow.py）
    # ---------------------------
    def get_action_outputs(self, action_name: str) -> list[dict]:
        """取得 Action 宣告的輸出：[{key, name, desc, type}]，依宣告順序。"""
        return self._cached("outputs", action_name, lambda: self.kg.query(
            """
            MATCH (a:Action {name:$name})-[r:HAS_OUTPUT]->(o:Output)
            RETURN o.key AS key, o.name AS name, o.desc AS desc, o.type AS type
            ORDER BY coalesce(r.order, 999), o.key
            """,
            {"name": action_name},
        ) or [])

    # ---------------------------
    # Vector search
    # ---------------------------
    def search_actions_by_vector(
        self, *, vector: List[float], top_k: int, min_score: float
    ) -> List[Dict[str, Any]]:
        """
        優先走 adapter 的 vector_query_nodes。
        若 adapter 回空，fallback 直接用 db.index.vector.queryNodes。
        """
        # 1) primary
        primary_failed = False
        try:
            rows = self.kg.vector_query_nodes(
                index_name=ACTION_DESC_INDEX,
                vector=vector,
                top_k=top_k,
                min_score=min_score,
                return_props=["name", "description"],
            )
            if rows:
                return rows
        except Exception:
            rows = []
            primary_failed = True

        # 2) fallback (Neo4j 5+)
        try:
            cypher = """
            CALL db.index.vector.queryNodes($index_name, $top_k, $vector)
            YIELD node, score
            WHERE score >= $min_score
            RETURN
                node.name AS name,
                node.description AS description,
                score AS score,
                id(node) AS id
            ORDER BY score DESC
            """
            return self.kg.query(
                cypher,
                {
                    "index_name": ACTION_DESC_INDEX,
                    "top_k": int(top_k),
                    "vector": vector,
                    "min_score": float(min_score),
                },
            )
        except Exception:
            if primary_failed:
                # 兩種查詢都失敗（例如索引被刪除）：下次比對時重新檢查索引
                self._index_ready_dim = None
            return []


# ---------------------------
# Seed helpers
# ---------------------------
CLEAR_ACTION_CONDITIONS = "MATCH (n) WHERE n:Precondition OR n:Effect DETACH DELETE n"
CLEAR_ACTION_OUTPUTS = "MATCH (o:Output) DETACH DELETE o"


def write_action_conditions(write, action_id: str, *, preconditions=(), effects=()) -> None:
    """seed 用：寫入 Action 的前置條件與效果（每筆建立新節點；seed 前先執行 CLEAR_ACTION_CONDITIONS）。

    write(cypher, params) 為 adapter 的寫入函式。欄位意義見 src/core/intent/preconditions.py。
    """
    for spec in preconditions:
        write(
            """
            MATCH (a:Action {id:$aid})
            CREATE (a)-[:REQUIRES]->(:Precondition {
                key: $key, arg: $arg, op: $op, value: $value, desc: $desc, severity: $severity
            })
            """,
            {
                "aid": action_id,
                "key": spec["key"],
                "arg": spec.get("arg"),
                "op": spec.get("op", "eq"),
                "value": spec.get("value"),
                "desc": spec.get("desc", ""),
                "severity": spec.get("severity", "block"),
            },
        )
    for spec in effects:
        write(
            """
            MATCH (a:Action {id:$aid})
            CREATE (a)-[:PRODUCES]->(:Effect {key: $key, arg: $arg, value: $value, value_arg: $value_arg, desc: $desc})
            """,
            {
                "aid": action_id,
                "key": spec["key"],
                "arg": spec.get("arg"),
                "value": spec.get("value"),
                "value_arg": spec.get("value_arg"),
                "desc": spec.get("desc", ""),
            },
        )


def write_action_outputs(write, action_id: str, outputs=()) -> None:
    """seed 用：寫入 Action 宣告的輸出（seed 前先執行 CLEAR_ACTION_OUTPUTS）。

    執行端在結果的 result 欄位提供這些輸出；欄位意義見 src/core/intent/dataflow.py。
    """
    for order, spec in enumerate(outputs):
        write(
            """
            MATCH (a:Action {id:$aid})
            CREATE (a)-[:HAS_OUTPUT {order: $order}]->(:Output {key: $key, name: $name, desc: $desc, type: $type})
            """,
            {
                "aid": action_id,
                "order": order,
                "key": spec["key"],
                "name": spec.get("name", ""),
                "desc": spec.get("desc", ""),
                "type": spec.get("type", "string"),
            },
        )

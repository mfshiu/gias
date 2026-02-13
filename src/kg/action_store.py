# src/kg/action_store.py
from __future__ import annotations

from typing import Any, Dict, List


class ActionStore:
    def __init__(self, kg_adapter):
        self.kg = kg_adapter

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

    def ensure_action_desc_index(self, dimensions: int) -> None:
        """
        確保 action_desc_vec 存在且 vector.dimensions 正確。
        若維度不符（或無法判斷），會 DROP + CREATE。
        並等待 index ONLINE。
        """
        name = "action_desc_vec"
        idx = self._show_index(name)

        need_recreate = False
        if idx:
            existing_dim = self._extract_vector_dimensions(idx)

            # ✅ 抓不到就重建（你目前情境更需要「確保一致」）
            if existing_dim is None or int(existing_dim) != int(dimensions):
                need_recreate = True

        if need_recreate:
            # ✅ 加 IF EXISTS，避免不存在就噴錯
            self.kg.query(f"DROP INDEX {name} IF EXISTS", {})
            idx = None

        if not idx:
            # ✅ 加 IF NOT EXISTS，避免重跑測試噴錯
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
            )

        # ✅ 最關鍵：等 ONLINE
        self._await_index_online(name)

    # ---------------------------
    # Params schema
    # ---------------------------
    def get_action_params(self, action_name: str) -> list[dict]:
        cypher = """
        MATCH (a:Action {name:$name})-[:HAS_PARAM]->(p:Param)
        RETURN
            p.key AS key,
            p.name AS name,
            p.desc AS desc,
            p.type AS type,
            p.required AS required,
            p.enum AS enum,
            p.example AS example
        ORDER BY coalesce(p.required,false) DESC, p.key ASC
        """
        return self.kg.query(cypher, {"name": action_name})

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
        try:
            rows = self.kg.vector_query_nodes(
                index_name="action_desc_vec",
                vector=vector,
                top_k=top_k,
                min_score=min_score,
                return_props=["name", "description"],
            )
            if rows:
                return rows
        except Exception:
            rows = []

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
                    "index_name": "action_desc_vec",
                    "top_k": int(top_k),
                    "vector": vector,
                    "min_score": float(min_score),
                },
            )
        except Exception:
            return []

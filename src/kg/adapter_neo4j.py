# src/kg/adapter_neo4j.py
"""
Neo4j/Bolt 實作（Adapter）

定位：
- 封裝 neo4j driver / session / transaction 細節
- 提供統一的 read / write / query 執行入口（回傳 list[dict]）
- 處理：database、fetch_size、timeout、重試（暫時性錯誤）、關閉資源

擴充（本次新增）：
- ✅ 修正「卡住」：補齊 driver connection timeout / acquisition timeout（避免卡在 Bolt socket recv / pool wait）
- ✅ 修正 query/tx timeout：使用 tx.run(timeout=...)（neo4j python driver v5 相容）
- ✅ 補齊 query() 介面：兼容 ActionStore / Matcher（避免 'Neo4jBoltAdapter' object has no attribute 'query'）
- ✅ ensure_vector_index：若 index 存在但 dimensions 不同，會 drop + recreate
- 支援 Cypher 端向量相似度查詢（vector_query_nodes / vector_query_relationships）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
import time
import re

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired

JsonDict = Dict[str, Any]
Params = Dict[str, Any]


# -------------------------
# Config
# -------------------------
@dataclass(frozen=True)
class Neo4jAdapterConfig:
    uri: str
    user: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None
    encrypted: bool = False

    fetch_size: int = 2000

    # ⚠️ 重要：避免卡住
    connection_timeout_sec: int = 10
    acquisition_timeout_sec: int = 10

    # ✅ query / tx timeout（秒）
    timeout_sec: int = 15

    max_retries: int = 2
    retry_backoff_sec: float = 0.5


# -------------------------
# Adapter
# -------------------------
class Neo4jBoltAdapter:
    """
    Neo4j Bolt Adapter：唯一負責 driver / session / tx / retry。
    """

    def __init__(self, config: Neo4jAdapterConfig, logger: Optional[Any] = None):
        self.config = config
        self._logger = logger

        auth = None
        if self.config.user is not None:
            auth = (self.config.user or "", self.config.password or "")

        # ✅ 避免卡住：交由 driver 控制連線與連線池等待時間
        # 注意：不同 neo4j driver 版本對 kwargs 支援不一樣，故採 try/fallback
        driver_kwargs = dict(
            auth=auth,
            encrypted=self.config.encrypted,
            connection_timeout=float(self.config.connection_timeout_sec),
            connection_acquisition_timeout=float(self.config.acquisition_timeout_sec),
        )

        try:
            self._driver = GraphDatabase.driver(self.config.uri, **driver_kwargs)
        except TypeError as e:
            # 某些環境/driver 版本可能不支援部分 kwargs
            self._log(
                "warning",
                f"Neo4j driver kwargs not fully supported ({e}). Falling back to minimal timeouts.",
            )
            self._driver = GraphDatabase.driver(
                self.config.uri,
                auth=auth,
                encrypted=self.config.encrypted,
                connection_timeout=float(self.config.connection_timeout_sec),
            )

    @classmethod
    def from_config(cls, kg_cfg: dict, logger=None) -> "Neo4jBoltAdapter":
        """
        從 gias.toml 的 [kg.neo4j] 設定建立 adapter
        """
        cfg = Neo4jAdapterConfig(
            uri=kg_cfg["uri"],
            user=kg_cfg.get("user"),
            password=kg_cfg.get("password"),
            database=kg_cfg.get("database"),
            encrypted=kg_cfg.get("encrypted", False),
            fetch_size=kg_cfg.get("fetch_size", 2000),
            timeout_sec=kg_cfg.get("timeout_sec", 15),
            max_retries=kg_cfg.get("max_retries", 2),
            retry_backoff_sec=kg_cfg.get("retry_backoff_sec", 0.5),
            connection_timeout_sec=kg_cfg.get("connection_timeout_sec", 10),
            acquisition_timeout_sec=kg_cfg.get("acquisition_timeout_sec", 10),
        )
        return cls(cfg, logger=logger)

    # -------------------------
    # Lifecycle
    # -------------------------
    def close(self) -> None:
        if getattr(self, "_driver", None) is not None:
            try:
                self._driver.close()
            finally:
                self._driver = None

    def __enter__(self) -> "Neo4jBoltAdapter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -------------------------
    # Public APIs
    # -------------------------
    def read(self, cypher: str, params: Optional[Params] = None) -> List[JsonDict]:
        """
        Read-only query.
        Return: list[dict] (each record -> dict)
        """
        return self._run_with_retry(
            op_name="read",
            runner=lambda session: self._run(session, cypher, params or {}, write=False),
        )

    def write(self, cypher: str, params: Optional[Params] = None) -> List[JsonDict]:
        """
        Write query.
        Return: list[dict] (each record -> dict)
        """
        return self._run_with_retry(
            op_name="write",
            runner=lambda session: self._run(session, cypher, params or {}, write=True),
        )

    def query(self, cypher: str, params: Optional[Params] = None, *, write: bool = False) -> List[JsonDict]:
        """
        ✅ 兼容介面：ActionStore / Matcher 常用 query()。
        預設視為 read；若 write=True 則走 write。
        """
        if write:
            return self.write(cypher, params)
        return self.read(cypher, params)

    # -------------------------
    # Vector Index / Vector Query APIs
    # -------------------------
    def ensure_vector_index(
        self,
        *,
        index_name: str,
        label: str,
        embedding_prop: str,
        dimensions: int,
        similarity: str = "cosine",
        drop_if_dimension_mismatch: bool = True,
    ) -> None:
        """
        確保 Neo4j Vector Index 存在（Neo4j 5.x）
        - 若已存在且 dimensions 不同，預設會 drop + recreate（避免你之前的 dims=8 汙染）
        """
        if not index_name:
            raise ValueError("index_name is empty")
        if not label:
            raise ValueError("label is empty")
        if not embedding_prop:
            raise ValueError("embedding_prop is empty")
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")

        sim = (similarity or "cosine").lower()
        if sim not in ("cosine", "euclidean"):
            raise ValueError("similarity must be 'cosine' or 'euclidean'")

        idx = self._escape_identifier(index_name)
        lab = self._escape_identifier(label)
        prop = self._escape_identifier(embedding_prop)

        # ✅ 若 index 存在且 dimensions 不同 → drop
        try:
            existing_dim = self._get_vector_index_dimensions(index_name)
            if (
                drop_if_dimension_mismatch
                and existing_dim is not None
                and int(existing_dim) != int(dimensions)
            ):
                self._log(
                    "warning",
                    f"Vector index '{index_name}' dimension mismatch: existing={existing_dim}, want={dimensions}. Dropping.",
                )
                self.write(f"DROP INDEX {idx} IF EXISTS")
        except Exception as e:
            # SHOW INDEXES 在部分版本/權限下可能失敗：不致命，繼續走 CREATE IF NOT EXISTS
            self._log("warning", f"ensure_vector_index: failed to inspect existing index: {e}")

        cypher = f"""
        CREATE VECTOR INDEX {idx} IF NOT EXISTS
        FOR (n:{lab})
        ON (n.{prop})
        OPTIONS {{
          indexConfig: {{
            `vector.dimensions`: {int(dimensions)},
            `vector.similarity_function`: '{sim}'
          }}
        }}
        """
        self.write(cypher)

    def vector_query_nodes(
        self,
        *,
        index_name: str,
        vector: List[float],
        top_k: int = 10,
        min_score: float = 0.0,
        return_props: Optional[List[str]] = None,
    ) -> List[JsonDict]:
        """
        CALL db.index.vector.queryNodes(indexName, k, vector) YIELD node, score
        """
        if top_k <= 0:
            return []
        if not isinstance(vector, list) or not vector:
            return []
        if not index_name:
            raise ValueError("index_name is empty")

        return_props = return_props or ["name", "description"]

        cols: List[str] = []
        for p in return_props:
            pp = self._escape_identifier(p)
            cols.append(f"node.{pp} AS {pp}")
        cols.append("score AS score")
        ret = ", ".join(cols)

        cypher = f"""
        CALL db.index.vector.queryNodes($index_name, $top_k, $vector)
        YIELD node, score
        WHERE score >= $min_score
        RETURN {ret}
        ORDER BY score DESC
        """
        return self.read(
            cypher,
            {
                "index_name": index_name,
                "top_k": int(top_k),
                "vector": vector,
                "min_score": float(min_score),
            },
        )

    def vector_query_relationships(
        self,
        *,
        index_name: str,
        vector: List[float],
        top_k: int = 10,
        min_score: float = 0.0,
        return_props: Optional[List[str]] = None,
    ) -> List[JsonDict]:
        """
        CALL db.index.vector.queryRelationships(indexName, k, vector) YIELD relationship, score
        """
        if top_k <= 0:
            return []
        if not isinstance(vector, list) or not vector:
            return []
        if not index_name:
            raise ValueError("index_name is empty")

        return_props = return_props or ["name", "description"]

        cols: List[str] = []
        for p in return_props:
            pp = self._escape_identifier(p)
            cols.append(f"relationship.{pp} AS {pp}")
        cols.append("score AS score")
        ret = ", ".join(cols)

        cypher = f"""
        CALL db.index.vector.queryRelationships($index_name, $top_k, $vector)
        YIELD relationship, score
        WHERE score >= $min_score
        RETURN {ret}
        ORDER BY score DESC
        """
        return self.read(
            cypher,
            {
                "index_name": index_name,
                "top_k": int(top_k),
                "vector": vector,
                "min_score": float(min_score),
            },
        )

    # -------------------------
    # Internals
    # -------------------------
    def _run(self, session, cypher: str, params: Params, write: bool) -> List[JsonDict]:
        """
        用 execute_read/execute_write 執行，並在 tx.run 設定 timeout。
        """
        tx_timeout = float(self.config.timeout_sec)

        def _execute(tx):
            result = tx.run(cypher, params, timeout=tx_timeout)
            return [dict(r) for r in result]

        if write:
            return session.execute_write(_execute)
        return session.execute_read(_execute)

    def _run_with_retry(
        self,
        op_name: str,
        runner: Callable[[Any], List[JsonDict]],
    ) -> List[JsonDict]:
        for attempt in range(self.config.max_retries + 1):
            try:
                with self._driver.session(
                    database=self.config.database,
                    fetch_size=self.config.fetch_size,
                ) as session:
                    return runner(session)

            except (ServiceUnavailable, SessionExpired) as e:
                self._log(
                    "warning",
                    f"Neo4jBoltAdapter.{op_name} transient error: {e} (attempt={attempt}/{self.config.max_retries})",
                )
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_backoff_sec * (attempt + 1))
                    continue
                raise

            except Neo4jError as e:
                # Neo4jError 裡也可能是暫時性（例如 transient）— 但保守起見不做碼判斷，交給上層決策
                self._log("error", f"Neo4jBoltAdapter.{op_name} neo4j error: {e}")
                raise

            except Exception as e:
                self._log("error", f"Neo4jBoltAdapter.{op_name} unexpected error: {e}")
                raise

        return []

    def _get_vector_index_dimensions(self, index_name: str) -> Optional[int]:
        """
        讀取現有 vector index 的 dimensions（Neo4j 5.x）
        若找不到或欄位不可用，回傳 None
        """
        # SHOW INDEXES 欄位隨版本可能略有差異，但 options 通常存在
        cypher = """
        SHOW INDEXES
        YIELD name, type, options
        WHERE name = $name
        RETURN name, type, options
        """
        rows = self.read(cypher, {"name": index_name})
        if not rows:
            return None

        options = rows[0].get("options") or {}
        # 期待格式：options.indexConfig["vector.dimensions"]
        index_cfg = options.get("indexConfig") or {}
        dim = index_cfg.get("vector.dimensions")
        if dim is None:
            return None
        try:
            return int(dim)
        except Exception:
            return None

    def _log(self, level: str, msg: str) -> None:
        if not self._logger:
            return
        fn = getattr(self._logger, level, None)
        if callable(fn):
            fn(msg)

    # -------------------------
    # Helpers
    # -------------------------
    _IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def _escape_identifier(self, name: str) -> str:
        """
        Neo4j identifier 安全處理：
        - 英數底線：原樣
        - 否則加 backticks
        """
        if not name:
            raise ValueError("identifier is empty")
        if self._IDENT_RE.match(name):
            return name
        safe = name.replace("`", "``")
        return f"`{safe}`"


# -------------------------
# Factory (optional)
# -------------------------
def build_neo4j_adapter(
    uri: str,
    user: Optional[str] = None,
    password: Optional[str] = None,
    database: Optional[str] = None,
    encrypted: bool = False,
    fetch_size: int = 2000,
    timeout_sec: int = 15,
    max_retries: int = 2,
    retry_backoff_sec: float = 0.5,
    logger: Optional[Any] = None,
    connection_timeout_sec: int = 10,
    acquisition_timeout_sec: int = 10,
) -> Neo4jBoltAdapter:
    cfg = Neo4jAdapterConfig(
        uri=uri,
        user=user,
        password=password,
        database=database,
        encrypted=encrypted,
        fetch_size=fetch_size,
        timeout_sec=timeout_sec,
        max_retries=max_retries,
        retry_backoff_sec=retry_backoff_sec,
        connection_timeout_sec=connection_timeout_sec,
        acquisition_timeout_sec=acquisition_timeout_sec,
    )
    return Neo4jBoltAdapter(cfg, logger=logger)

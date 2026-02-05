# src/kg/adapter_neo4j.py
"""
Neo4j/Bolt 實作（Adapter）

定位：
- 封裝 neo4j driver / session / transaction 細節
- 提供統一的 read / write 執行入口（回傳 list[dict]）
- 處理：database、fetch_size、timeout、重試（暫時性錯誤）、關閉資源

擴充（本次新增）：
- 支援 Neo4j 5.x Vector Index 建立（ensure_vector_index）
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
    timeout_sec: int = 15          # server-side tx timeout
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

        # Neo4j no-auth 時可以不提供 auth
        auth = None
        if self.config.user is not None:
            auth = (self.config.user or "", self.config.password or "")

        self._driver = GraphDatabase.driver(
            self.config.uri,
            auth=auth,
            encrypted=self.config.encrypted,
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
        )
        return cls(cfg, logger=logger)

    # -------------------------
    # Lifecycle
    # -------------------------
    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()

    def __enter__(self) -> "Neo4jBoltAdapter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -------------------------
    # Public APIs
    # -------------------------
    def read(self, cypher: str, params: Optional[Params] = None) -> List[JsonDict]:
        """
        Read-only query. ⚠️ 不得包含 CREATE / MERGE / SET / DELETE 等寫入操作。
        Return: list[dict] (each record -> dict)
        """
        return self._run_with_retry(
            op_name="read",
            runner=lambda session: self._run(session, cypher, params or {}, write=False),
        )

    def write(self, cypher: str, params: Optional[Params] = None) -> List[JsonDict]:
        """
        Write query. 可包含 CREATE / MERGE / SET / DELETE 等寫入操作。
        Return: list[dict] (each record -> dict)
        """
        return self._run_with_retry(
            op_name="write",
            runner=lambda session: self._run(session, cypher, params or {}, write=True),
        )

    # -------------------------
    # Vector Index / Vector Query APIs (新增)
    # -------------------------
    def ensure_vector_index(
        self,
        *,
        index_name: str,
        label: str,
        embedding_prop: str,
        dimensions: int,
        similarity: str = "cosine",
    ) -> None:
        """
        確保 Neo4j Vector Index 存在（Neo4j 5.x）

        正確語法（Neo4j 5.x）：
        CREATE VECTOR INDEX <name> IF NOT EXISTS
        FOR (n:Label) ON (n.prop)
        OPTIONS { indexConfig: {`vector.dimensions`: <int>, `vector.similarity_function`: 'cosine' } }

        注意：DDL 對參數化支援不一致（尤其是 indexConfig 內），
        為避免 "Invalid input" 類問題，本方法直接將 dimensions/similarity 寫死在 Cypher。
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

        # ✅ Neo4j 5.x 正確語法（不要寫 CREATE VECTOR INDEX ... ON ... (沒有 FOR/ON 會錯）
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
        使用 Neo4j 原生向量查詢（在 DB 端算相似度）：
        CALL db.index.vector.queryNodes(indexName, k, vector) YIELD node, score

        本方法將 node 映射成你要的 props + score（避免 node 無法 JSON 化）
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
        若 embedding 放在 Relationship 上，可用：
        CALL db.index.vector.queryRelationships(indexName, k, vector) YIELD relationship, score

        注意：此 procedure 需 Neo4j 版本支援；不支援時會丟 Neo4jError。
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
        tx_timeout = self.config.timeout_sec

        def _execute(tx):
            # ✅ 注意：neo4j python driver 建議 tx.run(cypher, parameters=params)
            # 但你這版測試已經是 tx.run(cypher, params, timeout=...) 的寫法，這裡保持相容
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
        last_exc: Optional[Exception] = None

        for attempt in range(self.config.max_retries + 1):
            try:
                with self._driver.session(
                    database=self.config.database,
                    fetch_size=self.config.fetch_size,
                ) as session:
                    return runner(session)

            except (ServiceUnavailable, SessionExpired) as e:
                last_exc = e
                self._log("warning", f"Neo4jBoltAdapter.{op_name} transient error: {e} (attempt={attempt})")
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_backoff_sec * (attempt + 1))
                    continue
                raise

            except Neo4jError as e:
                self._log("error", f"Neo4jBoltAdapter.{op_name} neo4j error: {e}")
                raise

            except Exception as e:
                last_exc = e
                self._log("error", f"Neo4jBoltAdapter.{op_name} unexpected error: {e}")
                raise

        if last_exc:
            raise last_exc
        return []

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
    )
    return Neo4jBoltAdapter(cfg, logger=logger)

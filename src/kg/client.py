# src/kg/client.py
"""
KGClient（對外 API）

重整重點（你要的版本）：
- ✅ 上層（GIAS其他模組）只能透過 KGClient 存取 KG（禁止散落 Cypher）
- ✅ 讀寫分離：queries.py（read）/ commands.py（write）
- ✅ 低階連線/交易/重試/timeout 全交給 adapter_neo4j.py（避免職責重疊）
- ✅ KGClient 專心提供「語意層 API」與一致的回傳格式

相依：
- neo4j 官方驅動：pip install neo4j
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union
import os

from .adapter_neo4j import Neo4jBoltAdapter, Neo4jAdapterConfig
from . import queries as Q
from . import commands as C

JsonDict = Dict[str, Any]


# -------------------------
# Config
# -------------------------
@dataclass(frozen=True)
class KGClientConfig:
    uri: str
    user: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None  # Neo4j 5 可指定 database；不指定則用預設
    encrypted: bool = False

    # 行為設定
    max_retries: int = 2
    retry_backoff_sec: float = 0.5
    query_timeout_sec: int = 15  # 交易逾時（server-side）
    fetch_size: int = 2000


# -------------------------
# KGClient (Facade)
# -------------------------
class KGClient:
    """
    對外 API：提供「語意層」的 KG 操作，不直接暴露 Cypher 給上層。
    """

    def __init__(self, config: KGClientConfig, logger: Optional[Any] = None):
        self.config = config
        self._logger = logger

        adapter_cfg = Neo4jAdapterConfig(
            uri=config.uri,
            user=config.user,
            password=config.password,
            database=config.database,
            encrypted=config.encrypted,
            fetch_size=config.fetch_size,
            timeout_sec=config.query_timeout_sec,
            max_retries=config.max_retries,
            retry_backoff_sec=config.retry_backoff_sec,
        )
        self._adapter = Neo4jBoltAdapter(adapter_cfg, logger=logger)

    # -------------------------
    # Lifecycle
    # -------------------------
    def close(self) -> None:
        if self._adapter:
            self._adapter.close()

    def __enter__(self) -> "KGClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -------------------------
    # Low-level (internal use)
    # -------------------------
    def query_raw(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[JsonDict]:
        """
        低階讀取：保留給 debug 或特殊情境；一般請走語意 API。
        """
        return self._adapter.read(cypher, params or {})

    def command_raw(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[JsonDict]:
        """低階寫入：保留給 debug 或特殊情境；一般請走語意 API。"""
        return self._adapter.write(cypher, params or {})

    # -------------------------
    # Semantic APIs (GIAS-friendly)
    # -------------------------

    # 1) 概念錨定 / Grounding
    def grounding_candidates(
        self,
        terms: Sequence[str],
        label: str = "Concept",
        prop: str = "name",
        top_k: int = 10,
    ) -> List[JsonDict]:
        cypher, params = Q.grounding_candidates(terms=terms, label=label, prop=prop, top_k=top_k)
        return self._adapter.read(cypher, params)

    # 2) 取出某概念相關事實/片段（給 RAG）
    def get_facts_by_concept(
        self,
        concept_id: Union[int, str],
        fact_label: str = "Fact",
        rel: str = "ABOUT",
        top_k: int = 20,
    ) -> List[JsonDict]:
        cypher, params = Q.facts_by_concept(
            concept_id=int(concept_id),
            fact_label=fact_label,
            rel=rel,
            top_k=top_k,
        )
        return self._adapter.read(cypher, params)

    # 3) 前置條件檢查（提供 feasibility）
    def check_preconditions(
        self,
        action_name: str,
        context: Optional[Dict[str, Any]] = None,
        top_k: int = 100,
    ) -> JsonDict:
        cypher, params = Q.preconditions_by_action(action_name=action_name, top_k=top_k)
        rows = self._adapter.read(cypher, params)
        if not rows:
            result: JsonDict = {"action": action_name, "preconditions": []}
        else:
            result = rows[0]

        if context is not None:
            # 不在這裡做判斷，只回傳讓上層比對 ground truth
            result["context_echo"] = context
        return result

    # 4) 程序步驟（拆解用）
    def get_procedure_steps(self, goal_name: str, top_k: int = 50) -> List[JsonDict]:
        cypher, params = Q.procedure_steps_by_goal(goal_name=goal_name, top_k=top_k)
        return self._adapter.read(cypher, params)

    # -------------------------
    # Write APIs (safe wrappers)
    # -------------------------
    def upsert_concept(self, name: str, extra: Optional[Dict[str, Any]] = None) -> JsonDict:
        cypher, params = C.upsert_concept(name=name, extra=extra)
        rows = self._adapter.write(cypher, params)
        return rows[0] if rows else {"name": name}

    def create_fact(
        self,
        text: str,
        source: Optional[str] = None,
        page: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> JsonDict:
        cypher, params = C.create_fact(text=text, source=source, page=page, extra=extra)
        rows = self._adapter.write(cypher, params)
        return rows[0] if rows else {}

    def link_fact_to_concept(
        self,
        fact_text: str,
        concept_name: str,
        source: Optional[str] = None,
        page: Optional[int] = None,
        rel: str = "ABOUT",
    ) -> JsonDict:
        cypher, params = C.link_fact_to_concept_by_name(
            fact_text=fact_text,
            concept_name=concept_name,
            source=source,
            page=page,
            rel=rel,
        )
        rows = self._adapter.write(cypher, params)
        return rows[0] if rows else {}

    def link_nodes_by_id(
        self,
        from_id: int,
        to_id: int,
        rel: str,
        rel_props: Optional[Dict[str, Any]] = None,
    ) -> JsonDict:
        cypher, params = C.link_existing_nodes_by_id(from_id=from_id, to_id=to_id, rel=rel, rel_props=rel_props)
        rows = self._adapter.write(cypher, params)
        return rows[0] if rows else {}

    def delete_node_by_id(self, node_id: int, detach: bool = True) -> JsonDict:
        cypher, params = C.delete_node_by_id(node_id=node_id, detach=detach)
        self._adapter.write(cypher, params)
        return {"deleted": True, "node_id": node_id}


    # -------------------------
    # Convenience constructor
    # -------------------------
    @staticmethod
    def from_env(logger: Optional[Any] = None) -> "KGClient":
        """
        從環境變數讀取（建議放在 .env）
        - NEO4J_URI=bolt://localhost:7687
        - NEO4J_USER=neo4j
        - NEO4J_PASSWORD=pass
        - NEO4J_DATABASE=neo4j (可選)
        - NEO4J_ENCRYPTED=false
        - KG_MAX_RETRIES=2
        - KG_TIMEOUT_SEC=15
        - KG_FETCH_SIZE=2000
        """
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER")
        password = os.getenv("NEO4J_PASSWORD")
        database = os.getenv("NEO4J_DATABASE")
        encrypted = os.getenv("NEO4J_ENCRYPTED", "false").lower() in ("1", "true", "yes")

        max_retries = int(os.getenv("KG_MAX_RETRIES", "2"))
        timeout_sec = int(os.getenv("KG_TIMEOUT_SEC", "15"))
        fetch_size = int(os.getenv("KG_FETCH_SIZE", "2000"))

        cfg = KGClientConfig(
            uri=uri,
            user=user,
            password=password,
            database=database,
            encrypted=encrypted,
            max_retries=max_retries,
            retry_backoff_sec=float(os.getenv("KG_RETRY_BACKOFF_SEC", "0.5")),
            query_timeout_sec=timeout_sec,
            fetch_size=fetch_size,
        )
        return KGClient(cfg, logger=logger)

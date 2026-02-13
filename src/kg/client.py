# src/kg/client.py
"""
KGClient（對外 API）

設計原則（統一版）：
- ✅ 完全以 gias.toml（agent_config）為設定來源
- ❌ 不再讀 .env / os.getenv
- ✅ 上層只能透過 KGClient 存取 KG
- ✅ 低階連線 / retry / timeout 全交給 Neo4jBoltAdapter
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union

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
    database: Optional[str] = None
    encrypted: bool = False

    max_retries: int = 2
    retry_backoff_sec: float = 0.5
    query_timeout_sec: int = 15
    fetch_size: int = 2000


# -------------------------
# KGClient (Facade)
# -------------------------
class KGClient:
    """
    對外 API：提供「語意層」的 KG 操作。
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
    # Low-level (internal / debug)
    # -------------------------
    def query_raw(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[JsonDict]:
        return self._adapter.read(cypher, params or {})

    def command_raw(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[JsonDict]:
        return self._adapter.write(cypher, params or {})

    # -------------------------
    # Semantic APIs
    # -------------------------
    def grounding_candidates(
        self,
        terms: Sequence[str],
        label: str = "Concept",
        prop: str = "name",
        top_k: int = 10,
    ) -> List[JsonDict]:
        cypher, params = Q.grounding_candidates(terms=terms, label=label, prop=prop, top_k=top_k)
        return self._adapter.read(cypher, params)

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

    def check_preconditions(
        self,
        action_name: str,
        context: Optional[Dict[str, Any]] = None,
        top_k: int = 100,
    ) -> JsonDict:
        cypher, params = Q.preconditions_by_action(action_name=action_name, top_k=top_k)
        rows = self._adapter.read(cypher, params)
        result = rows[0] if rows else {"action": action_name, "preconditions": []}
        if context is not None:
            result["context_echo"] = context
        return result

    def get_procedure_steps(self, goal_name: str, top_k: int = 50) -> List[JsonDict]:
        cypher, params = Q.procedure_steps_by_goal(goal_name=goal_name, top_k=top_k)
        return self._adapter.read(cypher, params)

    # -------------------------
    # Write APIs
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
        cypher, params = C.link_existing_nodes_by_id(
            from_id=from_id,
            to_id=to_id,
            rel=rel,
            rel_props=rel_props,
        )
        rows = self._adapter.write(cypher, params)
        return rows[0] if rows else {}

    def delete_node_by_id(self, node_id: int, detach: bool = True) -> JsonDict:
        cypher, params = C.delete_node_by_id(node_id=node_id, detach=detach)
        self._adapter.write(cypher, params)
        return {"deleted": True, "node_id": node_id}

    # -------------------------
    # Factory (唯一入口)
    # -------------------------
    @staticmethod
    def from_config(agent_config: dict, logger: Optional[Any] = None) -> "KGClient":
        kg_cfg = agent_config.get("kg")
        if not isinstance(kg_cfg, dict):
            raise RuntimeError("Missing [kg] section in gias.toml")

        if kg_cfg.get("type") != "neo4j":
            raise RuntimeError(f"Unsupported KG type: {kg_cfg.get('type')}")

        neo = kg_cfg.get("neo4j")
        if not isinstance(neo, dict):
            raise RuntimeError("Missing [kg.neo4j] section in gias.toml")

        cfg = KGClientConfig(
            uri=neo["uri"],
            user=neo.get("user"),
            password=neo.get("password"),
            database=neo.get("database"),
            encrypted=neo.get("encrypted", False),
            fetch_size=kg_cfg.get("fetch_size", 2000),
            query_timeout_sec=kg_cfg.get("timeout_sec", 15),
            max_retries=kg_cfg.get("max_retries", 2),
            retry_backoff_sec=kg_cfg.get("retry_backoff_sec", 0.5),
        )
        return KGClient(cfg, logger=logger)

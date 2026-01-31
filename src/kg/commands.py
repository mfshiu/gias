# src/kg/commands.py
"""
寫入型 Command（update / modify）

原則：
- 只放「Cypher 字串 + 參數建構」的純函式（回傳 (cypher, params)）
- 不碰 driver / session（那些放在 adapter_neo4j.py）
- KGClient 只負責組合語意 API 與呼叫 adapter 執行

回傳型別：
- (cypher: str, params: dict)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

Params = Dict[str, Any]
CypherCommand = Tuple[str, Params]


# -------------------------
# Concept upsert / update
# -------------------------

def upsert_concept(
    name: str,
    extra: Optional[Dict[str, Any]] = None,
    label: str = "Concept",
    key_prop: str = "name",
) -> CypherCommand:
    """
    MERGE (c:Concept {name:$name})
    SET c += $extra
    """
    extra = extra or {}
    cypher = f"""
    MERGE (c:{label} {{{key_prop}: $name}})
    SET c += $extra
    RETURN id(c) AS node_id, c.{key_prop} AS name, labels(c) AS labels, properties(c) AS props
    """
    return cypher, {"name": name, "extra": extra}


def set_node_props_by_id(
    node_id: int,
    props: Dict[str, Any],
    mode: str = "merge",  # "merge" | "replace"
) -> CypherCommand:
    """
    以 id 更新節點屬性：
    - merge：SET n += $props（增量合併）
    - replace：SET n = $props（整包覆蓋）
    """
    if mode not in ("merge", "replace"):
        raise ValueError("mode must be 'merge' or 'replace'")

    set_clause = "SET n += $props" if mode == "merge" else "SET n = $props"
    cypher = f"""
    MATCH (n) WHERE id(n) = $id
    {set_clause}
    RETURN id(n) AS node_id, labels(n) AS labels, properties(n) AS props
    """
    return cypher, {"id": int(node_id), "props": props}


# -------------------------
# Fact create / link
# -------------------------

def create_fact(
    text: str,
    source: Optional[str] = None,
    page: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
    label: str = "Fact",
) -> CypherCommand:
    """
    CREATE (f:Fact {text, source, page, created_at})
    SET f += extra
    """
    extra = extra or {}
    cypher = f"""
    CREATE (f:{label} {{
      text: $text,
      source: $source,
      page: $page,
      created_at: datetime()
    }})
    SET f += $extra
    RETURN id(f) AS fact_id, f.text AS text, f.source AS source, f.page AS page, properties(f) AS props
    """
    return cypher, {"text": text, "source": source, "page": page, "extra": extra}


def link_fact_to_concept_by_name(
    fact_text: str,
    concept_name: str,
    source: Optional[str] = None,
    page: Optional[int] = None,
    rel: str = "ABOUT",
    concept_label: str = "Concept",
    concept_prop: str = "name",
    fact_label: str = "Fact",
    rel_props: Optional[Dict[str, Any]] = None,
    concept_extra: Optional[Dict[str, Any]] = None,
    fact_extra: Optional[Dict[str, Any]] = None,
    fact_key: Optional[str] = None,   # ⭐ 新增：用於 Fact 去重（如 hash）
) -> CypherCommand:
    """
    一次完成：
    - MERGE Concept（可帶 concept_extra）
    - CREATE 或 MERGE Fact（若提供 fact_key 則 MERGE，否則 CREATE）
    - MERGE 關係（可帶 rel_props）

    設計說明：
    - fact_key=None：維持原本行為（每次 CREATE 新 Fact）
    - fact_key!=None：以 Fact.key 做唯一性，避免重複事實爆量
    """

    rel_props = rel_props or {}
    concept_extra = concept_extra or {}
    fact_extra = fact_extra or {}

    cypher = f"""
    // 1) Concept
    MERGE (c:{concept_label} {{{concept_prop}: $cname}})
    SET c += $cextra

    // 2) Fact（依是否有 fact_key 決定 CREATE 或 MERGE）
    FOREACH (_ IN CASE WHEN $fkey IS NOT NULL THEN [1] ELSE [] END |
        MERGE (f:{fact_label} {{key: $fkey}})
        SET f.text = $text,
            f.source = $source,
            f.page = $page,
            f += $fextra
        ON CREATE SET f.created_at = datetime()
    )
    FOREACH (_ IN CASE WHEN $fkey IS NULL THEN [1] ELSE [] END |
        CREATE (f:{fact_label} {{
            text: $text,
            source: $source,
            page: $page,
            created_at: datetime()
        }})
        SET f += $fextra
    )

    // 3) Relationship
    MERGE (f)-[r:{rel}]->(c)
    SET r += $rprops

    RETURN id(f) AS fact_id,
           id(c) AS concept_id,
           type(r) AS rel_type,
           properties(r) AS rel_props
    """

    return cypher, {
        "cname": concept_name,
        "text": fact_text,
        "source": source,
        "page": page,
        "cextra": concept_extra,
        "fextra": fact_extra,
        "rprops": rel_props,
        "fkey": fact_key,
    }


def link_existing_nodes_by_id(
    from_id: int,
    to_id: int,
    rel: str,
    rel_props: Optional[Dict[str, Any]] = None,
) -> CypherCommand:
    """
    MATCH (a) WHERE id(a)=$from
    MATCH (b) WHERE id(b)=$to
    MERGE (a)-[r:REL]->(b)
    SET r += rel_props
    """
    rel_props = rel_props or {}
    cypher = f"""
    MATCH (a) WHERE id(a) = $from
    MATCH (b) WHERE id(b) = $to
    MERGE (a)-[r:{rel}]->(b)
    SET r += $props
    RETURN type(r) AS rel_type, properties(r) AS rel_props, id(a) AS from_id, id(b) AS to_id
    """
    return cypher, {"from": int(from_id), "to": int(to_id), "props": rel_props}


# -------------------------
# Delete / cleanup
# -------------------------

def delete_node_by_id(node_id: int, detach: bool = True) -> CypherCommand:
    """
    刪除節點：
    - detach=True：DETACH DELETE（會一併移除關係）
    - detach=False：DELETE（若有關係會失敗）
    """
    clause = "DETACH DELETE n" if detach else "DELETE n"
    cypher = f"""
    MATCH (n) WHERE id(n) = $id
    {clause}
    """
    return cypher, {"id": int(node_id)}


def delete_relationships_between(
    a_id: int,
    b_id: int,
    rel: Optional[str] = None,
    direction: str = "out",  # out: (a)-[r]->(b), in: (b)-[r]->(a), both: undirected
) -> CypherCommand:
    """
    刪除兩點之間的關係（可限定 rel type）
    """
    if direction not in ("out", "in", "both"):
        raise ValueError("direction must be 'out', 'in', or 'both'")

    rel_part = f":{rel}" if rel else ""
    if direction == "in":
        pattern = f"(b)-[r{rel_part}]->(a)"
    elif direction == "both":
        pattern = f"(a)-[r{rel_part}]-(b)"
    else:
        pattern = f"(a)-[r{rel_part}]->(b)"

    cypher = f"""
    MATCH (a) WHERE id(a) = $a
    MATCH (b) WHERE id(b) = $b
    MATCH {pattern}
    DELETE r
    RETURN count(r) AS deleted
    """
    return cypher, {"a": int(a_id), "b": int(b_id)}

# src/kg/queries.py
"""
讀取型 Query（grounding / lookup）

原則：
- 只放「Cypher 字串 + 參數建構」的純函式（回傳 (cypher, params)）
- 不碰 driver / session（那些放在 adapter_neo4j.py）
- KGClient 呼叫本檔取得 (cypher, params)，再交給 adapter 執行

建議使用方式：
from . import queries as Q
cypher, params = Q.grounding_candidates(["廢棄物", "清運"])
rows = kg.query_raw(cypher, params)  # 或由 KGClient 的語意 API 包起來
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

Params = Dict[str, Any]
CypherQuery = Tuple[str, Params]


# -------------------------
# Grounding / Concept lookup
# -------------------------

def grounding_candidates(
    terms: Sequence[str],
    label: str = "Concept",
    prop: str = "name",
    top_k: int = 10,
) -> CypherQuery:
    """
    對多個 terms 找 Concept 候選（contains 或 equals）。
    """
    cypher = f"""
    UNWIND $terms AS t
    MATCH (c:{label})
    WHERE toLower(c.{prop}) CONTAINS toLower(t)
       OR toLower(c.{prop}) = toLower(t)
    RETURN t AS term,
           c.{prop} AS name,
           id(c) AS node_id,
           labels(c) AS labels
    LIMIT $limit
    """
    return cypher, {"terms": list(terms), "limit": int(top_k)}


def concept_by_name(
    name: str,
    label: str = "Concept",
    prop: str = "name",
) -> CypherQuery:
    """
    以 name 精準取 Concept 節點（大小寫不敏感）。
    """
    cypher = f"""
    MATCH (c:{label})
    WHERE toLower(c.{prop}) = toLower($name)
    RETURN id(c) AS node_id,
           c.{prop} AS name,
           labels(c) AS labels,
           properties(c) AS props
    LIMIT 1
    """
    return cypher, {"name": name}


def concept_neighbors(
    concept_id: int,
    rel_types: Optional[Sequence[str]] = None,
    direction: str = "both",  # "out" | "in" | "both"
    top_k: int = 50,
) -> CypherQuery:
    """
    取 Concept 週邊鄰居，用於快速展開子圖（debug / RAG context）。
    rel_types: 只限定特定關係型別（None 代表不限）
    direction: out / in / both
    """
    if direction not in ("out", "in", "both"):
        raise ValueError("direction must be 'out', 'in', or 'both'")

    rel_filter = ""
    if rel_types is None:
        rel_filter = ""
    elif not rel_types:
        raise ValueError("rel_types=[] is not allowed; use None for no restriction")
    else:
        rel_filter = ":" + "|".join(rel_types)

    if direction == "out":
        pattern = f"(c)-[r{rel_filter}]->(n)"
    elif direction == "in":
        pattern = f"(n)-[r{rel_filter}]->(c)"
    else:
        pattern = f"(c)-[r{rel_filter}]-(n)"

    cypher = f"""
    MATCH (c) WHERE id(c) = $cid
    MATCH {pattern}
    RETURN id(n) AS node_id,
           labels(n) AS labels,
           properties(n) AS props,
           type(r) AS rel_type,
           properties(r) AS rel_props
    LIMIT $limit
    """
    return cypher, {"cid": int(concept_id), "limit": int(top_k)}


# -------------------------
# Facts / Evidence lookup (for RAG)
# -------------------------

def facts_by_concept(
    concept_id: int,
    fact_label: str = "Fact",
    rel: str = "ABOUT",
    top_k: int = 20,
) -> CypherQuery:
    """
    (f:Fact)-[:ABOUT]->(c) 取出與 Concept 相關的 facts。

    修正重點：
    - 移除 OPTIONAL MATCH，避免回傳 fact_id/text/source/page 全為 None 的「空 record」
    - 若 Concept 不存在或沒有任何 Fact，則回傳空列表（由上層決定怎麼處理）
    """
    cypher = f"""
    MATCH (c) WHERE id(c) = $cid
    MATCH (f:{fact_label})-[:{rel}]->(c)
    RETURN id(f) AS fact_id,
           f.text AS text,
           f.source AS source,
           f.page AS page,
           properties(f) AS props
    LIMIT $limit
    """
    return cypher, {"cid": int(concept_id), "limit": int(top_k)}


def facts_search_text(
    keyword: str,
    fact_label: str = "Fact",
    prop: str = "text",
    top_k: int = 20,
) -> CypherQuery:
    """
    在 Fact.text 做 contains 搜尋（簡易版）。
    若日後改用 fulltext index，可替換此查詢即可。
    """
    cypher = f"""
    MATCH (f:{fact_label})
    WHERE toLower(f.{prop}) CONTAINS toLower($kw)
       OR toLower(f.{prop}) = toLower($kw)
    RETURN id(f) AS fact_id,
           f.{prop} AS text,
           f.source AS source,
           f.page AS page,
           properties(f) AS props
    LIMIT $limit
    """
    return cypher, {"kw": keyword, "limit": int(top_k)}


# -------------------------
# Procedure / Plan lookup (for intention decomposition)
# -------------------------

def procedure_steps_by_goal(goal_name: str, top_k: int = 50) -> CypherQuery:
    """
    範例 schema：
    (g:Goal {name})-[:HAS_PROCEDURE]->(p:Procedure)-[:HAS_STEP]->(s:Step {order, text})
    """
    cypher = """
    MATCH (g:Goal {name:$goal})-[:HAS_PROCEDURE]->(p:Procedure)-[:HAS_STEP]->(s:Step)
    RETURN id(s) AS step_id,
           s.order AS order,
           s.text AS text,
           properties(s) AS props
    ORDER BY s.order ASC
    LIMIT $limit
    """
    return cypher, {"goal": goal_name, "limit": int(top_k)}


def subgoals_by_goal(
    goal_name: str,
    rel: str = "HAS_SUBGOAL",
    top_k: int = 50,
) -> CypherQuery:
    """
    範例 schema：
    (g:Goal)-[:HAS_SUBGOAL]->(sg:Goal)
    """
    cypher = f"""
    MATCH (g:Goal {{name:$goal}})-[:{rel}]->(sg:Goal)
    RETURN id(sg) AS subgoal_id,
           sg.name AS name,
           properties(sg) AS props
    LIMIT $limit
    """
    return cypher, {"goal": goal_name, "limit": int(top_k)}


# -------------------------
# Preconditions / Constraints lookup
# -------------------------

def preconditions_by_action(action_name: str, top_k: int = 100) -> CypherQuery:
    """
    範例 schema：
    (a:Action {name})-[:REQUIRES]->(p:Precondition {key, op, value, desc})
    """
    cypher = """
    MATCH (a:Action {name:$name})
    OPTIONAL MATCH (a)-[:REQUIRES]->(p:Precondition)
    RETURN a.name AS action,
           collect({
             id: id(p),
             key: p.key,
             op: p.op,
             value: p.value,
             desc: p.desc,
             props: properties(p)
           }) AS preconditions
    LIMIT $limit
    """
    return cypher, {"name": action_name, "limit": int(top_k)}


def conflicts_between_intents(
    intent_ids: Sequence[int],
    rel: str = "CONFLICTS_WITH",
    top_k: int = 200,
) -> CypherQuery:
    """
    查一組意圖之間是否存在衝突邊：
    (i:Intent)-[:CONFLICTS_WITH]->(j:Intent)

    回傳 pair 列表，方便上層做衝突圖分析。
    """
    cypher = f"""
    UNWIND $ids AS iid
    MATCH (i:Intent) WHERE id(i) = iid
    MATCH (i)-[r:{rel}]-(j:Intent)
    WHERE id(j) IN $ids
    RETURN id(i) AS i_id,
           id(j) AS j_id,
           type(r) AS rel_type,
           properties(r) AS rel_props
    LIMIT $limit
    """
    return cypher, {"ids": [int(x) for x in intent_ids], "limit": int(top_k)}


# -------------------------
# Utility / Debug helpers
# -------------------------

def node_by_id(node_id: int) -> CypherQuery:
    """
    Debug 用：用 id 取節點與屬性。
    """
    cypher = """
    MATCH (n) WHERE id(n) = $id
    RETURN id(n) AS node_id, labels(n) AS labels, properties(n) AS props
    """
    return cypher, {"id": int(node_id)}


def relationship_sample(rel_type: str, top_k: int = 50) -> CypherQuery:
    """
    Debug 用：抽樣某種關係型別的邊。
    """
    cypher = f"""
    MATCH (a)-[r:{rel_type}]->(b)
    RETURN id(a) AS a_id, labels(a) AS a_labels,
           id(b) AS b_id, labels(b) AS b_labels,
           properties(r) AS rel_props
    LIMIT $limit
    """
    return cypher, {"limit": int(top_k)}

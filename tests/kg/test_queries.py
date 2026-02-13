import pytest

from src.kg import queries as Q


# -------------------------
# Helpers
# -------------------------

def assert_has_all(text: str, parts):
    for p in parts:
        assert p in text, f"Missing part in cypher: {p}"


# -------------------------
# Grounding / Concept lookup
# -------------------------

def test_grounding_candidates_basic():
    cypher, params = Q.grounding_candidates(["廢棄物", "清運"], top_k=7)

    assert_has_all(cypher, [
        "UNWIND $terms AS t",
        "MATCH (c:Concept)",
        "toLower(c.name) CONTAINS toLower(t)",
        "RETURN t AS term",
        "LIMIT $limit",
    ])
    assert params["terms"] == ["廢棄物", "清運"]
    assert params["limit"] == 7


def test_grounding_candidates_custom_label_prop():
    cypher, params = Q.grounding_candidates(["abc"], label="MyLabel", prop="title", top_k=3)

    assert "MATCH (c:MyLabel)" in cypher
    assert "c.title" in cypher
    assert params["terms"] == ["abc"]
    assert params["limit"] == 3


def test_concept_by_name():
    cypher, params = Q.concept_by_name("事業廢棄物")

    assert_has_all(cypher, [
        "MATCH (c:Concept)",
        "toLower(c.name) = toLower($name)",
        "LIMIT 1",
    ])
    assert params == {"name": "事業廢棄物"}


def test_concept_neighbors_default():
    cypher, params = Q.concept_neighbors(10)

    assert "MATCH (c) WHERE id(c) = $cid" in cypher
    assert "MATCH (c)-[r]-(n)" in cypher or "MATCH (c)-[r]-(n)" in cypher.replace(" ", "")
    assert params["cid"] == 10
    assert params["limit"] == 50


def test_concept_neighbors_out_direction():
    cypher, params = Q.concept_neighbors(10, direction="out", top_k=9)

    assert "MATCH (c)-[r]->(n)" in cypher
    assert params["cid"] == 10
    assert params["limit"] == 9


def test_concept_neighbors_in_direction():
    cypher, params = Q.concept_neighbors(10, direction="in", top_k=9)

    assert "MATCH (n)-[r]->(c)" in cypher
    assert params["cid"] == 10
    assert params["limit"] == 9


def test_concept_neighbors_invalid_direction_raises():
    with pytest.raises(ValueError):
        Q.concept_neighbors(1, direction="sideways")


def test_concept_neighbors_rel_types():
    cypher, params = Q.concept_neighbors(10, rel_types=["ABOUT", "REQUIRES"], direction="both", top_k=5)

    # r:ABOUT|REQUIRES
    assert "r:ABOUT|REQUIRES" in cypher
    assert params["cid"] == 10
    assert params["limit"] == 5


# -------------------------
# Facts / Evidence lookup
# -------------------------

def test_facts_by_concept_no_optional():
    cypher, params = Q.facts_by_concept(3, top_k=11)

    assert "MATCH (c) WHERE id(c) = $cid" in cypher
    assert "MATCH (f:Fact)-[:ABOUT]->(c)" in cypher  # 確保不是 OPTIONAL MATCH
    assert "OPTIONAL MATCH" not in cypher.upper()
    assert params["cid"] == 3
    assert params["limit"] == 11


def test_facts_by_concept_custom_label_rel():
    cypher, params = Q.facts_by_concept(3, fact_label="Evidence", rel="MENTIONS", top_k=2)

    assert "MATCH (f:Evidence)-[:MENTIONS]->(c)" in cypher
    assert params["cid"] == 3
    assert params["limit"] == 2


def test_facts_search_text():
    cypher, params = Q.facts_search_text("焚化", top_k=8)

    assert "MATCH (f:Fact)" in cypher
    assert "toLower(f.text) CONTAINS toLower($kw)" in cypher
    assert "LIMIT $limit" in cypher
    assert params["kw"] == "焚化"
    assert params["limit"] == 8


# -------------------------
# Procedure / Plan lookup
# -------------------------

def test_procedure_steps_by_goal():
    cypher, params = Q.procedure_steps_by_goal("清運流程", top_k=6)

    assert_has_all(cypher, [
        "MATCH (g:Goal {name:$goal})",
        "RETURN id(s) AS step_id",
        "ORDER BY s.order ASC",
        "LIMIT $limit",
    ])
    assert params["goal"] == "清運流程"
    assert params["limit"] == 6


def test_subgoals_by_goal():
    cypher, params = Q.subgoals_by_goal("母目標", rel="HAS_SUBGOAL", top_k=4)

    assert "MATCH (g:Goal {name:$goal})-[:HAS_SUBGOAL]->(sg:Goal)" in cypher
    assert params["goal"] == "母目標"
    assert params["limit"] == 4


# -------------------------
# Preconditions / Constraints lookup
# -------------------------

def test_preconditions_by_action():
    cypher, params = Q.preconditions_by_action("清運廢棄物", top_k=12)

    assert_has_all(cypher, [
        "MATCH (a:Action {name:$name})",
        "OPTIONAL MATCH (a)-[:REQUIRES]->(p:Precondition)",
        "collect({",
        "}) AS preconditions",
        "LIMIT $limit",
    ])
    assert params["name"] == "清運廢棄物"
    assert params["limit"] == 12


def test_conflicts_between_intents():
    cypher, params = Q.conflicts_between_intents([1, 2, 3], top_k=99)

    assert_has_all(cypher, [
        "UNWIND $ids AS iid",
        "MATCH (i:Intent) WHERE id(i) = iid",
        "CONFLICTS_WITH",
        "WHERE id(j) IN $ids",
        "LIMIT $limit",
    ])
    assert params["ids"] == [1, 2, 3]
    assert params["limit"] == 99


# -------------------------
# Utility / Debug helpers
# -------------------------

def test_node_by_id():
    cypher, params = Q.node_by_id(77)

    assert "MATCH (n) WHERE id(n) = $id" in cypher
    assert "RETURN id(n) AS node_id" in cypher
    assert params["id"] == 77


def test_relationship_sample():
    cypher, params = Q.relationship_sample("ABOUT", top_k=13)

    assert "MATCH (a)-[r:ABOUT]->(b)" in cypher
    assert "LIMIT $limit" in cypher
    assert params["limit"] == 13

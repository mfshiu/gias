import pytest

from src.kg import commands as C


# -------------------------
# Helpers
# -------------------------

def assert_has_all(text: str, parts):
    for p in parts:
        assert p in text, f"Missing part in cypher: {p}"


# -------------------------
# Concept upsert / update
# -------------------------

def test_upsert_concept_basic():
    cypher, params = C.upsert_concept("事業廢棄物", extra={"domain": "waste"})

    assert_has_all(cypher, [
        "MERGE (c:Concept",
        "SET c += $extra",
        "RETURN id(c) AS node_id",
    ])
    assert params["name"] == "事業廢棄物"
    assert params["extra"]["domain"] == "waste"


def test_upsert_concept_custom_label_key_prop():
    cypher, params = C.upsert_concept("X", label="MyConcept", key_prop="title")

    assert "MERGE (c:MyConcept {title:" in cypher
    assert params["name"] == "X"
    assert params["extra"] == {}


def test_set_node_props_by_id_merge():
    cypher, params = C.set_node_props_by_id(10, {"a": 1}, mode="merge")

    assert "MATCH (n) WHERE id(n) = $id" in cypher
    assert "SET n += $props" in cypher
    assert params["id"] == 10
    assert params["props"] == {"a": 1}


def test_set_node_props_by_id_replace():
    cypher, params = C.set_node_props_by_id(11, {"a": 2}, mode="replace")

    assert "SET n = $props" in cypher
    assert params["id"] == 11
    assert params["props"]["a"] == 2


def test_set_node_props_by_id_invalid_mode_raises():
    with pytest.raises(ValueError):
        C.set_node_props_by_id(1, {"x": 1}, mode="bad")


# -------------------------
# Fact create / link
# -------------------------

def test_create_fact_basic():
    cypher, params = C.create_fact("必須依法清運", source="法規", page=12, extra={"tag": "law"})

    assert_has_all(cypher, [
        "CREATE (f:Fact",
        "created_at: datetime()",
        "SET f += $extra",
        "RETURN id(f) AS fact_id",
    ])
    assert params["text"] == "必須依法清運"
    assert params["source"] == "法規"
    assert params["page"] == 12
    assert params["extra"]["tag"] == "law"


def test_create_fact_custom_label():
    cypher, params = C.create_fact("x", label="Evidence")

    assert "CREATE (f:Evidence" in cypher
    assert params["text"] == "x"


def test_link_fact_to_concept_by_name_without_fact_key_create_path():
    cypher, params = C.link_fact_to_concept_by_name(
        fact_text="需申報",
        concept_name="事業廢棄物",
        source="法規",
        page=3,
        rel="ABOUT",
        fact_key=None,  # 強制走 CREATE path
    )

    assert_has_all(cypher, [
        "MERGE (c:Concept",
        "SET c += $cextra",
        "FOREACH (_ IN CASE WHEN $fkey IS NULL THEN [1] ELSE [] END |",
        "CREATE (f:Fact",
        "MERGE (f)-[r:ABOUT]->(c)",
        "SET r += $rprops",
        "RETURN id(f) AS fact_id",
    ])
    assert params["cname"] == "事業廢棄物"
    assert params["text"] == "需申報"
    assert params["source"] == "法規"
    assert params["page"] == 3
    assert params["fkey"] is None


def test_link_fact_to_concept_by_name_with_fact_key_merge_path():
    cypher, params = C.link_fact_to_concept_by_name(
        fact_text="需申報",
        concept_name="事業廢棄物",
        source="法規",
        page=3,
        rel="ABOUT",
        fact_key="hash123",  # 走 MERGE path
        rel_props={"w": 1},
        concept_extra={"cx": 1},
        fact_extra={"fx": 2},
    )

    assert_has_all(cypher, [
        "FOREACH (_ IN CASE WHEN $fkey IS NOT NULL THEN [1] ELSE [] END |",
        "MERGE (f:Fact {key: $fkey})",
        "ON CREATE SET f.created_at = datetime()",
        "SET r += $rprops",
        "RETURN id(f) AS fact_id",
    ])
    assert params["fkey"] == "hash123"
    assert params["rprops"]["w"] == 1
    assert params["cextra"]["cx"] == 1
    assert params["fextra"]["fx"] == 2


def test_link_existing_nodes_by_id_basic():
    cypher, params = C.link_existing_nodes_by_id(1, 2, "CAUSES", rel_props={"score": 0.8})

    assert_has_all(cypher, [
        "MATCH (a) WHERE id(a) = $from",
        "MATCH (b) WHERE id(b) = $to",
        "MERGE (a)-[r:CAUSES]->(b)",
        "SET r += $props",
        "RETURN type(r) AS rel_type",
    ])
    assert params["from"] == 1
    assert params["to"] == 2
    assert params["props"]["score"] == 0.8


# -------------------------
# Delete / cleanup
# -------------------------

def test_delete_node_by_id_detach_true_has_detach_delete():
    cypher, params = C.delete_node_by_id(9, detach=True)

    assert "DETACH DELETE n" in cypher
    assert params["id"] == 9


def test_delete_node_by_id_detach_false_has_delete():
    cypher, params = C.delete_node_by_id(9, detach=False)

    assert "DELETE n" in cypher
    assert "DETACH DELETE n" not in cypher
    assert params["id"] == 9


def test_delete_relationships_between_out_default():
    cypher, params = C.delete_relationships_between(1, 2)

    assert_has_all(cypher, [
        "MATCH (a) WHERE id(a) = $a",
        "MATCH (b) WHERE id(b) = $b",
        "MATCH (a)-[r]->(b)",
        "DELETE r",
        "RETURN count(r) AS deleted",
    ])
    assert params["a"] == 1
    assert params["b"] == 2


def test_delete_relationships_between_in():
    cypher, _ = C.delete_relationships_between(1, 2, direction="in")
    assert "MATCH (b)-[r]->(a)" in cypher


def test_delete_relationships_between_both():
    cypher, _ = C.delete_relationships_between(1, 2, direction="both")
    assert "MATCH (a)-[r]-(b)" in cypher


def test_delete_relationships_between_with_rel_type():
    cypher, _ = C.delete_relationships_between(1, 2, rel="ABOUT", direction="out")
    assert "MATCH (a)-[r:ABOUT]->(b)" in cypher


def test_delete_relationships_between_invalid_direction_raises():
    with pytest.raises(ValueError):
        C.delete_relationships_between(1, 2, direction="sideways")

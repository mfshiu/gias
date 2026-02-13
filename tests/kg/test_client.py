import pytest
from unittest.mock import MagicMock, patch

from src.kg.client import KGClient, KGClientConfig


# -------------------------
# Fixtures
# -------------------------

@pytest.fixture
def client_config():
    return KGClientConfig(
        uri="bolt://localhost:7687",
        user=None,
        password=None,
        database="neo4j",
        encrypted=False,
        max_retries=1,
        retry_backoff_sec=0.01,
        query_timeout_sec=5,
        fetch_size=100,
    )


@pytest.fixture
def mock_adapter():
    """
    模擬 Neo4jBoltAdapter，只保留 read / write。
    """
    adapter = MagicMock()
    adapter.read.return_value = []
    adapter.write.return_value = []
    return adapter


@pytest.fixture
def client(client_config, mock_adapter):
    """
    建立 KGClient，但把 adapter 換成 mock。
    """
    with patch("kg.client.Neo4jBoltAdapter", return_value=mock_adapter):
        yield KGClient(client_config)


# -------------------------
# Tests: lifecycle
# -------------------------

def test_client_close_calls_adapter_close(client, mock_adapter):
    client.close()
    mock_adapter.close.assert_called_once()


# -------------------------
# Tests: grounding / read APIs
# -------------------------

def test_grounding_candidates_calls_adapter_read(client, mock_adapter):
    mock_adapter.read.return_value = [
        {"term": "廢棄物", "name": "事業廢棄物", "node_id": 1}
    ]

    rows = client.grounding_candidates(["廢棄物"])

    assert rows[0]["name"] == "事業廢棄物"
    mock_adapter.read.assert_called_once()

    cypher, params = mock_adapter.read.call_args[0]
    assert "UNWIND $terms AS t" in cypher
    assert params["terms"] == ["廢棄物"]


def test_get_facts_by_concept(client, mock_adapter):
    mock_adapter.read.return_value = [
        {"fact_id": 10, "text": "依法需清運", "source": "法規"}
    ]

    rows = client.get_facts_by_concept(1)

    assert len(rows) == 1
    assert rows[0]["fact_id"] == 10
    mock_adapter.read.assert_called_once()

    cypher, params = mock_adapter.read.call_args[0]
    assert "MATCH (f:Fact)" in cypher
    assert params["cid"] == 1


def test_check_preconditions_no_result(client, mock_adapter):
    mock_adapter.read.return_value = []

    result = client.check_preconditions("清運廢棄物")

    assert result["action"] == "清運廢棄物"
    assert result["preconditions"] == []


def test_check_preconditions_with_context(client, mock_adapter):
    mock_adapter.read.return_value = [
        {
            "action": "清運廢棄物",
            "preconditions": [{"key": "license", "op": "=", "value": True}],
        }
    ]

    context = {"license": True}
    result = client.check_preconditions("清運廢棄物", context=context)

    assert result["context_echo"] == context
    assert result["preconditions"][0]["key"] == "license"


def test_get_procedure_steps(client, mock_adapter):
    mock_adapter.read.return_value = [
        {"order": 1, "text": "申請許可"},
        {"order": 2, "text": "安排清運"},
    ]

    rows = client.get_procedure_steps("清運流程")

    assert rows[0]["order"] == 1
    assert rows[1]["order"] == 2
    mock_adapter.read.assert_called_once()


# -------------------------
# Tests: write APIs
# -------------------------

def test_upsert_concept(client, mock_adapter):
    mock_adapter.write.return_value = [
        {"node_id": 5, "name": "事業廢棄物"}
    ]

    result = client.upsert_concept("事業廢棄物", {"domain": "waste"})

    assert result["name"] == "事業廢棄物"
    mock_adapter.write.assert_called_once()

    cypher, params = mock_adapter.write.call_args[0]
    assert "MERGE (c:Concept" in cypher
    assert params["name"] == "事業廢棄物"
    assert params["extra"]["domain"] == "waste"


def test_create_fact(client, mock_adapter):
    mock_adapter.write.return_value = [
        {"fact_id": 99, "text": "必須依法清運"}
    ]

    result = client.create_fact("必須依法清運", source="法規", page=12)

    assert result["fact_id"] == 99
    mock_adapter.write.assert_called_once()

    cypher, params = mock_adapter.write.call_args[0]
    assert "CREATE (f:Fact" in cypher
    assert params["text"] == "必須依法清運"


def test_link_fact_to_concept(client, mock_adapter):
    mock_adapter.write.return_value = [
        {"fact_id": 1, "concept_id": 2}
    ]

    result = client.link_fact_to_concept(
        fact_text="需申報",
        concept_name="事業廢棄物",
    )

    assert result["fact_id"] == 1
    assert result["concept_id"] == 2
    mock_adapter.write.assert_called_once()


def test_link_nodes_by_id(client, mock_adapter):
    mock_adapter.write.return_value = [
        {"rel_type": "CAUSES"}
    ]

    result = client.link_nodes_by_id(1, 2, "CAUSES")

    assert result["rel_type"] == "CAUSES"
    mock_adapter.write.assert_called_once()


def test_delete_node_by_id(client, mock_adapter):
    mock_adapter.write.return_value = []

    result = client.delete_node_by_id(123)

    assert result["deleted"] is True
    assert result["node_id"] == 123
    mock_adapter.write.assert_called_once()

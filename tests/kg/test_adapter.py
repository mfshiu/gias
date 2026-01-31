# tests/kg/test_adapter.py
import pytest
from unittest.mock import MagicMock, patch

from src.kg.adapter_neo4j import Neo4jBoltAdapter, Neo4jAdapterConfig


# -------------------------
# Fixtures
# -------------------------

@pytest.fixture
def adapter_config():
    return Neo4jAdapterConfig(
        uri="bolt://localhost:7687",
        user=None,              # no-auth
        password=None,
        database="neo4j",
        encrypted=False,
        fetch_size=100,
        timeout_sec=5,
        max_retries=1,
        retry_backoff_sec=0.01,
    )


@pytest.fixture
def mock_driver():
    return MagicMock()


@pytest.fixture
def adapter(adapter_config, mock_driver):
    with patch("kg.adapter_neo4j.GraphDatabase.driver", return_value=mock_driver):
        yield Neo4jBoltAdapter(adapter_config)


# -------------------------
# Helper to build fake session / tx
# -------------------------

def build_session_with_result(records):
    """
    建立 fake session，並把 tx 暴露出來方便 assert tx.run(...) 的呼叫參數。
    """
    mock_tx = MagicMock()
    mock_tx.run.return_value = records

    def _execute(fn):
        return fn(mock_tx)

    mock_session = MagicMock()
    mock_session.execute_read.side_effect = _execute
    mock_session.execute_write.side_effect = _execute

    # ⭐ 讓測試可以拿到 tx 做 assert
    mock_session._mock_tx = mock_tx
    return mock_session


# -------------------------
# Tests
# -------------------------

def test_read_executes_read_transaction(adapter, mock_driver):
    """read() 會呼叫 execute_read 並回傳 dict list"""
    fake_records = [{"a": 1}, {"a": 2}]
    fake_session = build_session_with_result(fake_records)

    mock_driver.session.return_value.__enter__.return_value = fake_session

    rows = adapter.read("MATCH (n) RETURN 1 AS a")

    assert rows == fake_records
    fake_session.execute_read.assert_called_once()
    fake_session.execute_write.assert_not_called()


def test_write_executes_write_transaction(adapter, mock_driver):
    """write() 會呼叫 execute_write"""
    fake_records = [{"x": "ok"}]
    fake_session = build_session_with_result(fake_records)

    mock_driver.session.return_value.__enter__.return_value = fake_session

    rows = adapter.write("CREATE (n) RETURN 'ok' AS x")

    assert rows == fake_records
    fake_session.execute_write.assert_called_once()
    fake_session.execute_read.assert_not_called()


def test_read_passes_cypher_and_params(adapter, mock_driver):
    """確認 cypher 與 params 會正確傳入 tx.run"""
    fake_records = [{"v": 42}]
    fake_session = build_session_with_result(fake_records)
    mock_driver.session.return_value.__enter__.return_value = fake_session

    cypher = "MATCH (n) WHERE n.x=$x RETURN n.x AS v"
    params = {"x": 42}

    adapter.read(cypher, params)

    fake_session._mock_tx.run.assert_called_with(
        cypher,
        params,
        timeout=adapter.config.timeout_sec,
    )


def test_retry_on_transient_error(adapter, mock_driver):
    """ServiceUnavailable 會觸發 retry"""
    from neo4j.exceptions import ServiceUnavailable

    # 第一次 session 失敗，第二次成功
    bad_session = MagicMock()
    bad_session.execute_read.side_effect = ServiceUnavailable("boom")

    good_records = [{"ok": True}]
    good_session = build_session_with_result(good_records)

    mock_driver.session.side_effect = [
        MagicMock(__enter__=MagicMock(return_value=bad_session), __exit__=MagicMock()),
        MagicMock(__enter__=MagicMock(return_value=good_session), __exit__=MagicMock()),
    ]

    rows = adapter.read("RETURN 1 AS ok")

    assert rows == good_records
    assert mock_driver.session.call_count == 2


def test_no_retry_on_neo4j_error(adapter, mock_driver):
    """Neo4jError 不應 retry"""
    from neo4j.exceptions import Neo4jError

    bad_session = MagicMock()
    bad_session.execute_read.side_effect = Neo4jError("syntax error")

    mock_driver.session.return_value.__enter__.return_value = bad_session

    with pytest.raises(Neo4jError):
        adapter.read("BROKEN CYPHER")

    assert mock_driver.session.call_count == 1


def test_close_closes_driver(adapter, mock_driver):
    """close() 會關閉 driver"""
    adapter.close()
    mock_driver.close.assert_called_once()

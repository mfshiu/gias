"""比對動作前的向量索引檢查（P-02）：只檢查一次、不刪除索引。"""

from __future__ import annotations

import logging

import pytest

from src.kg.action_store import ActionStore


def _index_row(dims=3, state="ONLINE"):
    options = {"indexConfig": {"vector.dimensions": dims}} if dims is not None else "unparsed"
    return {"name": "action_desc_vec", "state": state, "options": options}


class _KG:
    def __init__(self, index=None, *, search_fails=False, primary_fails=False):
        self.index = index
        self.search_fails = search_fails
        self.primary_fails = primary_fails
        self.queries: list[str] = []

    def query(self, cypher, params=None, *, write=False):
        kind = cypher.strip().split()[0].upper()
        self.queries.append(kind)
        if kind == "SHOW":
            return [self.index] if self.index else []
        if kind == "CREATE":
            self.index = _index_row(params["d"], "POPULATING")
            return []
        if kind == "CALL" and "awaitIndex" in cypher:
            self.index = dict(self.index, state="ONLINE")
            return []
        if kind == "CALL":
            if self.search_fails:
                raise RuntimeError("no such index")
            return [{"name": "LocateExhibit", "score": 0.9}]
        raise AssertionError(f"unexpected query {cypher}")

    def vector_query_nodes(self, **kw):
        if self.search_fails or self.primary_fails:
            raise RuntimeError("no such index")
        return [{"name": "LocateExhibit", "score": 0.9}]


def test_online_index_is_checked_once():
    kg = _KG(_index_row())
    store = ActionStore(kg)
    store.ensure_action_desc_index(3)
    store.ensure_action_desc_index(3)
    assert kg.queries == ["SHOW"]                    # 不等待、不重建，第二次不再查詢


def test_index_with_unknown_dimensions_is_kept():
    kg = _KG(_index_row(dims=None))
    ActionStore(kg).ensure_action_desc_index(3)
    assert kg.queries == ["SHOW"]


def test_dimension_mismatch_is_reported_not_rebuilt(caplog):
    kg = _KG(_index_row(dims=1536))
    with caplog.at_level(logging.ERROR, logger="src.kg.action_store"):
        ActionStore(kg).ensure_action_desc_index(3)
    assert kg.queries == ["SHOW"]
    assert "re-run the action seed" in caplog.text


def test_missing_index_is_created_and_awaited():
    kg = _KG(None)
    store = ActionStore(kg)
    store.ensure_action_desc_index(3)
    store.ensure_action_desc_index(3)
    assert kg.queries == ["SHOW", "CREATE", "CALL"]


def test_index_still_populating_is_awaited():
    kg = _KG(_index_row(state="POPULATING"))
    ActionStore(kg).ensure_action_desc_index(3)
    assert kg.queries == ["SHOW", "CALL"]


def test_new_dimension_is_checked_again():
    kg = _KG(_index_row())
    store = ActionStore(kg)
    store.ensure_action_desc_index(3)
    store.ensure_action_desc_index(4)
    assert kg.queries == ["SHOW", "SHOW"]


def test_failed_search_makes_the_next_match_check_again():
    kg = _KG(_index_row(), search_fails=True)
    store = ActionStore(kg)
    store.ensure_action_desc_index(3)
    assert store.search_actions_by_vector(vector=[0.1, 0.2, 0.3], top_k=5, min_score=0.0) == []
    store.ensure_action_desc_index(3)
    assert kg.queries.count("SHOW") == 2


def test_fallback_search_success_keeps_the_index_checked():
    kg = _KG(_index_row(), primary_fails=True)
    store = ActionStore(kg)
    store.ensure_action_desc_index(3)
    assert store.search_actions_by_vector(vector=[0.1, 0.2, 0.3], top_k=5, min_score=0.0)
    store.ensure_action_desc_index(3)
    assert kg.queries.count("SHOW") == 1


def test_kg_error_is_raised_and_not_remembered():
    kg = _KG(_index_row())
    store = ActionStore(kg)
    kg.query = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("neo4j down"))
    with pytest.raises(RuntimeError):
        store.ensure_action_desc_index(3)
    assert store._index_ready_dim is None

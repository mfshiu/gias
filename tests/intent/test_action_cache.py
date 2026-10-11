"""Action 查詢的批次與快取（P-01）。"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.intent.action_selector import ActionSelector
from src.core.intent.planner import RecursivePlanner
from src.kg.action_store import ActionStore
from src.kg.cache import TTLCache

PARAMS = {
    "RecommendExhibits": [{"key": "interests", "required": True, "order": 0},
                          {"key": "limit", "required": False, "order": 2},
                          {"key": "current_location", "required": False, "order": 1}],
    "LocateExhibit": [{"key": "target_name", "required": True, "order": 1},
                      {"key": "target_type", "required": True, "order": 0}],
}
OUTPUTS = {"RecommendExhibits": [{"key": "booths", "type": "list[string]", "desc": "推薦的攤位"}]}


class _KG:
    """記錄每次查詢；依 Cypher 內容回傳假資料。"""

    def __init__(self, fail=False):
        self.queries = []
        self.fail = fail

    def query(self, cypher, params=None, **kw):
        self.queries.append(cypher)
        if self.fail:
            raise RuntimeError("neo4j down")
        params = params or {}
        if "UNWIND" in cypher and "HAS_PARAM" in cypher:
            return [{"action": n, **p} for n in params["names"] for p in PARAMS.get(n, [])]
        if "UNWIND" in cypher and "HAS_OUTPUT" in cypher:
            return [{"action": n, **o} for n in params["names"] for o in OUTPUTS.get(n, [])]
        if "HAS_PARAM" in cypher:
            return list(PARAMS.get(params["name"], []))
        if "HAS_OUTPUT" in cypher:
            return list(OUTPUTS.get(params["name"], []))
        if "REQUIRES" in cypher:
            return [{"preconditions": [], "effects": []}]
        return []

    def read(self, cypher, params=None):
        return self.query(cypher, params)


def _match(name):
    return SimpleNamespace(action=SimpleNamespace(name=name, description=f"{name} desc"), score=0.9)


# ----------------------------------------------------------------------
# 選動作：所有 Action 的參數與輸出以兩個批次查詢取得
# ----------------------------------------------------------------------
def test_selector_queries_kg_twice_for_any_number_of_actions():
    kg = _KG()
    sel = ActionSelector(kg=kg, matcher=None, logger=logging.getLogger("t"))
    out = sel._to_prompt_format([_match("RecommendExhibits"), _match("LocateExhibit"), _match("AnswerFAQ")])
    assert len(kg.queries) == 2
    assert list(out) == [
        "RecommendExhibits(Interests, CurrentLocation, Limit)",     # 依參數宣告順序
        "LocateExhibit(TargetType, TargetName)",
        "AnswerFAQ()",
    ]
    assert out["RecommendExhibits(Interests, CurrentLocation, Limit)"].endswith(
        "| Outputs: booths (list[string]): 推薦的攤位; message: text reply")
    sel._to_prompt_format([_match("RecommendExhibits"), _match("AnswerFAQ")])
    assert len(kg.queries) == 2                                     # 都在快取中


def test_selector_degrades_when_kg_fails():
    sel = ActionSelector(kg=_KG(fail=True), matcher=None, logger=logging.getLogger("t"))
    assert sel._to_prompt_format([_match("LocateExhibit")]) == {"LocateExhibit()": "LocateExhibit desc"}


# ----------------------------------------------------------------------
# ActionStore 快取
# ----------------------------------------------------------------------
def test_store_caches_lookups_and_shares_them_with_batches():
    kg = _KG()
    store = ActionStore(kg)
    store.get_actions_schema(["RecommendExhibits"])
    assert store.get_action_params("RecommendExhibits") == PARAMS["RecommendExhibits"]
    assert store.get_action_outputs("RecommendExhibits") == OUTPUTS["RecommendExhibits"]
    store.get_action_conditions("RecommendExhibits")
    store.get_action_conditions("RecommendExhibits")
    assert len(kg.queries) == 3                                      # 兩個批次 + 一次前置條件
    store.get_actions_schema(["RecommendExhibits", "LocateExhibit"])
    assert len(kg.queries) == 5                                      # 只查還沒快取的 LocateExhibit


def test_cached_values_are_copies():
    store = ActionStore(_KG())
    store.get_action_params("LocateExhibit").append({"key": "junk"})          # 查詢的結果
    store.get_action_params("LocateExhibit").append({"key": "junk"})          # 快取命中的結果
    assert store.get_action_params("LocateExhibit") == PARAMS["LocateExhibit"]


def test_cache_expires_can_be_cleared_or_disabled():
    kg = _KG()
    store = ActionStore(kg, cache_ttl_sec=60)
    with patch("src.kg.cache.time.monotonic", return_value=1000.0):
        store.get_action_params("LocateExhibit")
        store.get_action_params("LocateExhibit")
    assert len(kg.queries) == 1
    with patch("src.kg.cache.time.monotonic", return_value=1061.0):
        store.get_action_params("LocateExhibit")
    assert len(kg.queries) == 2
    store.clear_cache()
    store.get_action_params("LocateExhibit")
    assert len(kg.queries) == 3

    uncached = ActionStore(kg, cache_ttl_sec=0)
    uncached.get_action_params("LocateExhibit")
    uncached.get_action_params("LocateExhibit")
    assert len(kg.queries) == 5


def test_failed_lookup_is_not_cached():
    kg = _KG(fail=True)
    store = ActionStore(kg)
    with pytest.raises(RuntimeError):
        store.get_action_params("LocateExhibit")
    kg.fail = False
    assert store.get_action_params("LocateExhibit") == PARAMS["LocateExhibit"]


# ----------------------------------------------------------------------
# Planner：同一個 Action 的 metadata 只查一次
# ----------------------------------------------------------------------
class _PlannerKG:
    def __init__(self):
        self.meta_queries = 0
        self.agent_queries = 0
        self.fail = False

    def query(self, cypher, params=None, **kw):
        self.meta_queries += 1
        if self.fail:
            raise RuntimeError("down")
        return [{"action_id": "a1", "topic": "navigation.request", "task": params["action_name"]}]

    def read(self, cypher, params=None):
        self.agent_queries += 1
        return []


class _Decomposer:
    def decompose(self, intent, available_actions, context=None):
        return {"sub_intents": [
            {"id": str(i), "intent": f"go {i}", "is_atomic": True, "atomic_source": "pre_defined",
             "action": f'LocateExhibit(TargetName="B{i}")'} for i in (1, 2, 3)
        ], "relationships": []}


def test_planner_looks_up_each_action_once():
    kg = _PlannerKG()
    planner = RecursivePlanner(decomposer=_Decomposer(), logger=logging.getLogger("t"), kg=kg)
    plan = planner.plan("tour", {})
    planner.plan("tour again", {})
    assert [n["task"] for n in plan["sub_plans"]] == ["LocateExhibit"] * 3
    assert (kg.meta_queries, kg.agent_queries) == (1, 1)
    plan["sub_plans"][0]["params_schema"].append("junk")                 # 各節點拿到的是副本
    assert plan["sub_plans"][1]["params_schema"] == []


def test_planner_does_not_cache_failed_lookups():
    kg = _PlannerKG()
    kg.fail = True
    planner = RecursivePlanner(decomposer=_Decomposer(), logger=logging.getLogger("t"), kg=kg)
    assert "task" not in planner.plan("tour", {})["sub_plans"][0]
    kg.fail = False
    assert planner.plan("tour", {})["sub_plans"][0]["task"] == "LocateExhibit"


def test_ttl_cache_basics():
    cache = TTLCache(10)
    assert cache.get("k") == (False, None)
    cache.put("k", None)                     # None 也可以快取（例如 Action 沒有對應的 Agent）
    assert cache.get("k") == (True, None)

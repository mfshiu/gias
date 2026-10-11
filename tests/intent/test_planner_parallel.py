"""Planner 平行拆解（P-05）：沒有先後的非原子子意圖同時拆解、不再固定 sleep。"""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import patch

import pytest

from src.core.intent.planner import RecursivePlanner


def _sub(sid, intent, action=""):
    return {"id": sid, "intent": intent, "is_atomic": bool(action),
            "atomic_source": "pre_defined" if action else None, "action": action}


class _Decomposer:
    """root 拆成數個非原子子意圖；每個子意圖拆成一個原子步驟（拆解時模擬 LLM 延遲）。"""

    def __init__(self, children, relationships=(), delay=0.1, delays=None, fail=None):
        self.children = children
        self.relationships = list(relationships)
        self.delay = delay
        self.delays = delays or {}
        self.fail = fail
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        self.contexts = {}
        self.finished = []

    def decompose(self, intent, available_actions, context=None):
        if intent == "root":
            return {"sub_intents": [_sub(cid, cid) for cid in self.children], "relationships": self.relationships}
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.contexts[intent] = context
        try:
            delay = self.delays.get(intent, self.delay)
            if delay:
                time.sleep(delay)
            if intent == self.fail:
                raise RuntimeError(f"decompose {intent} failed")
            return {"sub_intents": [_sub("1", f"do {intent}", f"Act{intent}()")], "relationships": []}
        finally:
            with self.lock:
                self.active -= 1
                self.finished.append(intent)


class _KG:
    """Action metadata：讓原子步驟綁定 task（才會成為後續步驟可引用的來源）。"""

    def query(self, cypher, params=None, **kw):
        return [{"action_id": "a", "topic": "info.request", "task": params["action_name"]}]

    def read(self, cypher, params=None):
        return []


def _planner(dec, max_parallel):
    return RecursivePlanner(decomposer=dec, logger=logging.getLogger("t"), kg=_KG(), max_parallel=max_parallel)


def test_unordered_sub_intents_are_decomposed_concurrently():
    dec = _Decomposer(["A", "B", "C"])
    started = time.monotonic()
    plan = _planner(dec, 3).plan("root", {})
    assert dec.max_active == 3
    assert time.monotonic() - started < 0.25                  # 逐一處理至少 0.3 秒
    assert [n["id"] for n in plan["sub_plans"]] == ["A", "B", "C"]


def test_concurrency_is_capped_by_max_parallel():
    dec = _Decomposer(["A", "B", "C", "D"])
    _planner(dec, 2).plan("root", {})
    assert dec.max_active == 2
    one = _Decomposer(["A", "B"], delay=0.0)
    _planner(one, 1).plan("root", {})
    assert one.max_active == 1


def test_ordered_sub_intents_wait_for_their_predecessors():
    seq = [{"type": "Sequence", "from_id": "A", "to_id": "B"}]
    dec = _Decomposer(["A", "B", "C"], relationships=seq, delays={"A": 0.15, "C": 0.0})
    plan = _planner(dec, 3).plan("root", {})
    assert dec.finished.index("A") < dec.finished.index("B")
    # B 在 A 之後的那一批拆解：可引用 A 子樹的步驟，以及前一批已建好、與 B 沒有先後的 C；
    # C 與 A 同一批拆解，沒有可引用的前面步驟
    assert [s["id"] for s in dec.contexts["B"]["upstream_outputs"]] == ["A.1", "C.1"]
    assert dec.contexts["C"] is None
    assert [n["id"] for n in plan["sub_plans"]] == ["A", "B", "C"]


def test_result_order_follows_the_decomposition_not_completion():
    dec = _Decomposer(["A", "B"], delays={"A": 0.15, "B": 0.0})
    plan = _planner(dec, 2).plan("root", {})
    assert dec.finished == ["B", "A"]
    assert [n["sub_plans"][0]["action"] for n in plan["sub_plans"]] == ["ActA()", "ActB()"]


def test_no_fixed_sleep_between_sub_intents():
    dec = _Decomposer(["A", "B"], delay=0.0)
    with patch("time.sleep", side_effect=AssertionError("planner must not sleep")):
        _planner(dec, 1).plan("root", {})


def test_errors_in_parallel_branches_propagate():
    dec = _Decomposer(["A", "B"], fail="B")
    with pytest.raises(RuntimeError, match="decompose B failed"):
        _planner(dec, 2).plan("root", {})


class _NestedDecomposer(_Decomposer):
    """root → A、B；A → A1、A2；B → B1、B2（都沒有先後）；孫層拆成原子步驟並模擬 LLM 延遲。"""

    TREE = {"root": ["A", "B"], "A": ["A1", "A2"], "B": ["B1", "B2"]}

    def decompose(self, intent, available_actions, context=None):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if intent in self.TREE:
                return {"sub_intents": [_sub(c, c) for c in self.TREE[intent]], "relationships": []}
            time.sleep(0.1)
            return {"sub_intents": [_sub("1", f"do {intent}", f"Act{intent}()")], "relationships": []}
        finally:
            with self.lock:
                self.active -= 1


def test_nested_parallel_branches_share_the_llm_call_limit():
    dec = _NestedDecomposer([])
    plan = _planner(dec, 2).plan("root", {})
    assert dec.max_active == 2                                # 巢狀平行時同時進行的拆解仍不超過上限
    assert [c["id"] for c in plan["sub_plans"][1]["sub_plans"]] == ["B.B1", "B.B2"]

"""Intention 模型與持久化單元測試（A-01）。"""

from __future__ import annotations

import json
import logging

import pytest

from src.core.intention import (
    BlackboardIntentionStore,
    InMemoryIntentionStore,
    Intention,
    IntentionStatus,
    InvalidTransition,
)

S = IntentionStatus


def test_lifecycle_transitions_and_history():
    it = Intention(text="帶我去 AI 展區", priority=2, requester="u1")
    assert it.status == S.PENDING and it.history[0]["reason"] == "submitted"
    it.transition(S.PLANNING, "scheduled")
    it.transition(S.EXECUTING, "plan committed")
    it.transition(S.SUSPENDED, "preempted by int-x")
    it.transition(S.PLANNING, "resumed after preemption")
    it.transition(S.EXECUTING, "plan committed")
    it.transition(S.ACHIEVED, "all steps done")
    assert it.attempts == 2
    assert it.is_terminal
    assert [h["status"] for h in it.history] == [
        "pending", "planning", "executing", "suspended", "planning", "executing", "achieved"]


@pytest.mark.parametrize("path", [
    [S.EXECUTING],                         # 不能跳過規劃
    [S.PLANNING, S.PENDING],
    [S.PLANNING, S.FAILED, S.PLANNING],    # 結束後不能再轉換
    [S.SUSPENDED],
])
def test_invalid_transitions_raise(path):
    it = Intention(text="x")
    with pytest.raises(InvalidTransition):
        for status in path:
            it.transition(status)


def test_any_unfinished_status_can_be_interrupted():
    for path in ([], [S.PLANNING], [S.PLANNING, S.EXECUTING], [S.PLANNING, S.SUSPENDED]):
        it = Intention(text="x")
        for status in path:
            it.transition(status)
        it.transition(S.INTERRUPTED, "shutdown")
        assert it.is_terminal


def test_to_dict():
    it = Intention(text="x", priority=1)
    d = it.to_dict()
    assert d["status"] == "pending" and d["priority"] == 1 and len(d["history"]) == 1
    assert "history" not in it.to_dict(with_history=False)


# ----------------------------------------------------------------------
# Store
# ----------------------------------------------------------------------
def test_in_memory_store_marks_unfinished_interrupted():
    store = InMemoryIntentionStore()
    done, running = Intention(text="a"), Intention(text="b")
    done.transition(S.PLANNING)
    done.transition(S.FAILED, "x")
    running.transition(S.PLANNING)
    store.save(done)
    store.save(running)
    store.mark_unfinished_interrupted("restart")
    assert store.records[done.intention_id]["status"] == "failed"
    assert store.records[running.intention_id]["status"] == "interrupted"


def test_blackboard_store_writes_in_order_with_json_fields():
    writes = []
    store = BlackboardIntentionStore(lambda c, p: writes.append((c, p)) or True, manager="m1")
    store.mark_unfinished_interrupted("restart")
    it = Intention(text="去 VR 展區", priority=3)
    it.plan_steps = [{"id": "1", "task": "LocateExhibit", "params": {"target_name": "VR_Area"}}]
    store.save(it)
    assert store.flush(2)
    store.close()

    (c1, p1), (c2, p2) = writes
    assert "SET i.status = 'interrupted'" in c1
    assert p1["manager"] == "m1" and "achieved" in p1["terminal"] and "pending" not in p1["terminal"]
    assert "MERGE (i:Intention {manager: $manager, intention_id: $intention_id})" in c2
    assert p2["status"] == "pending" and p2["priority"] == 3
    assert json.loads(p2["plan_steps"])[0]["task"] == "LocateExhibit"
    assert json.loads(p2["history"])[0]["status"] == "pending"


def test_blackboard_store_failures_do_not_raise(caplog):
    def failing_write(cypher, params):
        raise RuntimeError("broker down")

    store = BlackboardIntentionStore(failing_write, manager="m1", logger=logging.getLogger("store-test"))
    with caplog.at_level(logging.DEBUG, logger="store-test"):
        for _ in range(3):
            store.save(Intention(text="x"))
        assert store.flush(2)
    store.close()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1          # 只警告一次，之後降為 debug

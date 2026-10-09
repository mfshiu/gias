"""PlanCursor 單元測試。"""

from __future__ import annotations

from src.core.monitoring.cursor import PlanCursor
from src.core.monitoring.events import NodeState


def _simple_plan_sequence_two_atomics() -> dict:
    return {
        "id": "root",
        "intent": "test",
        "type": "composite",
        "sub_plans": [
            {"id": "a", "type": "atomic", "is_atomic": True, "task": "T1", "intent": "ia", "sub_plans": []},
            {"id": "b", "type": "atomic", "is_atomic": True, "task": "T2", "intent": "ib", "sub_plans": []},
        ],
        "execution_logic": [
            {"type": "Sequence", "from_id": "a", "to_id": "b"},
        ],
    }


def _parallel_two_atomics() -> dict:
    return {
        "id": "root",
        "intent": "test",
        "type": "composite",
        "sub_plans": [
            {"id": "x", "type": "atomic", "is_atomic": True, "task": "T1", "intent": "ix", "sub_plans": []},
            {"id": "y", "type": "atomic", "is_atomic": True, "task": "T2", "intent": "iy", "sub_plans": []},
        ],
        "execution_logic": [],
    }


def _nested_plan() -> dict:
    return {
        "id": "root",
        "intent": "root",
        "type": "composite",
        "sub_plans": [
            {
                "id": "L2-1",
                "type": "composite",
                "sub_plans": [
                    {"id": "a", "type": "atomic", "is_atomic": True, "task": "QueryZone", "sub_plans": []},
                    {"id": "b", "type": "atomic", "is_atomic": True, "task": "CheckZoneStatus", "sub_plans": []},
                ],
                "execution_logic": [{"type": "Sequence", "from_id": "a", "to_id": "b"}],
            },
            {
                "id": "L2-2",
                "type": "composite",
                "sub_plans": [
                    {"id": "c", "type": "atomic", "is_atomic": True, "task": "PlanRoute", "sub_plans": []},
                ],
                "execution_logic": [],
            },
        ],
        "execution_logic": [{"type": "Sequence", "from_id": "L2-1", "to_id": "L2-2"}],
    }


# ----------------------------------------------------------------------
# Indexing & ready
# ----------------------------------------------------------------------
def test_cursor_indexes_atomic_nodes():
    c = PlanCursor(_simple_plan_sequence_two_atomics())
    assert {r.node_id for r in c.all_records()} == {"a", "b"}
    for r in c.all_records():
        assert r.state == NodeState.PENDING


def test_cursor_next_ready_respects_sequence():
    c = PlanCursor(_simple_plan_sequence_two_atomics())
    ready = c.next_ready_atomics()
    assert [r.node_id for r in ready] == ["a"]


def test_cursor_next_ready_parallel_returns_all():
    c = PlanCursor(_parallel_two_atomics())
    ready = c.next_ready_atomics()
    assert {r.node_id for r in ready} == {"x", "y"}


def test_cursor_advances_after_first_done():
    c = PlanCursor(_simple_plan_sequence_two_atomics())
    c.mark_dispatched("a")
    c.mark_done("a", result={"ok": True})
    ready = c.next_ready_atomics()
    assert [r.node_id for r in ready] == ["b"]


def test_cursor_nested_advances_across_composites():
    c = PlanCursor(_nested_plan())
    # L2-1 atomic a 先 ready
    ids = [r.node_id for r in c.next_ready_atomics()]
    assert ids == ["a"]
    c.mark_dispatched("a"); c.mark_done("a", {})
    # b 接著 ready
    ids = [r.node_id for r in c.next_ready_atomics()]
    assert ids == ["b"]
    c.mark_dispatched("b"); c.mark_done("b", {})
    # 切到 L2-2 的 c
    ids = [r.node_id for r in c.next_ready_atomics()]
    assert ids == ["c"]


# ----------------------------------------------------------------------
# State transitions
# ----------------------------------------------------------------------
def test_mark_dispatched_returns_task_id():
    c = PlanCursor(_parallel_two_atomics())
    tid = c.mark_dispatched("x")
    assert isinstance(tid, str) and tid
    rec = c.record("x")
    assert rec.state == NodeState.IN_FLIGHT
    assert rec.task_id == tid
    assert rec.attempts == 1
    assert c.find_by_task_id(tid).node_id == "x"


def test_accept_action_result_routes_by_task_id():
    c = PlanCursor(_parallel_two_atomics())
    tid_x = c.mark_dispatched("x")
    rec = c.accept_action_result(tid_x, {"task_id": tid_x, "ok": True, "result": {"m": 1}})
    assert rec is not None
    assert c.record("x").state == NodeState.DONE


def test_accept_action_result_cancelled_marks_cancelled():
    c = PlanCursor(_parallel_two_atomics())
    tid = c.mark_dispatched("x")
    c.accept_action_result(tid, {"task_id": tid, "ok": False, "cancelled": True})
    assert c.record("x").state == NodeState.CANCELLED


def test_accept_action_result_failed_marks_failed():
    c = PlanCursor(_parallel_two_atomics())
    tid = c.mark_dispatched("x")
    c.accept_action_result(tid, {"task_id": tid, "ok": False, "error": "boom"})
    rec = c.record("x")
    assert rec.state == NodeState.FAILED
    assert rec.error == "boom"


def test_late_result_does_not_overwrite_obsolete():
    c = PlanCursor(_parallel_two_atomics())
    tid = c.mark_dispatched("x")
    c.mark_obsolete("x", reason="superseded")
    rec = c.accept_action_result(tid, {"task_id": tid, "ok": True})
    assert rec is not None
    assert c.record("x").state == NodeState.OBSOLETE  # not promoted to DONE


# ----------------------------------------------------------------------
# Retry / replan
# ----------------------------------------------------------------------
def test_reset_for_retry_brings_node_back_to_pending():
    c = PlanCursor(_parallel_two_atomics())
    tid = c.mark_dispatched("x")
    c.mark_failed("x", "boom")
    c.reset_for_retry("x")
    rec = c.record("x")
    assert rec.state == NodeState.PENDING
    assert rec.task_id is None
    assert rec.error is None
    assert rec.attempts == 1   # still 1 until next dispatch


def test_reset_for_retry_overrides_params():
    c = PlanCursor(_parallel_two_atomics())
    c.mark_dispatched("x")
    c.mark_failed("x", "boom")
    c.reset_for_retry("x", new_params={"new_key": "v"})
    assert c.record("x").node["params"] == {"new_key": "v"}


def test_replace_subtree_obsoletes_in_flight_and_indexes_new():
    c = PlanCursor(_nested_plan())
    tid = c.mark_dispatched("a")
    new_sub = {
        "id": "L2-1-new",
        "type": "composite",
        "sub_plans": [
            {"id": "na", "type": "atomic", "is_atomic": True, "task": "T_new", "sub_plans": []},
        ],
        "execution_logic": [],
    }
    ok = c.replace_subtree("L2-1", new_sub)
    assert ok is True
    # 原 a IN_FLIGHT → OBSOLETE
    assert c.record("a").state == NodeState.OBSOLETE
    # 原 b PENDING → SKIPPED
    assert c.record("b").state == NodeState.SKIPPED
    # 新 atomic 已索引且 PENDING
    assert c.record("na") is not None
    assert c.record("na").state == NodeState.PENDING


def test_replace_root_drops_old_pending_and_indexes_new():
    c = PlanCursor(_nested_plan())
    new_plan = {
        "id": "root2",
        "type": "composite",
        "sub_plans": [
            {"id": "newa", "type": "atomic", "is_atomic": True, "task": "T_new", "sub_plans": []},
        ],
        "execution_logic": [],
    }
    c.replace_root(new_plan)
    assert c.record("a").state == NodeState.SKIPPED
    assert c.record("newa").state == NodeState.PENDING
    ready = c.next_ready_atomics()
    assert [r.node_id for r in ready] == ["newa"]


# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------
def test_summary_marks_ok_when_all_done():
    c = PlanCursor(_simple_plan_sequence_two_atomics())
    for nid in ("a", "b"):
        tid = c.mark_dispatched(nid)
        c.accept_action_result(tid, {"task_id": tid, "ok": True})
    s = c.summary()
    assert s["ok"] is True
    assert s["state_counts"].get("done") == 2


def test_summary_not_ok_when_failure():
    c = PlanCursor(_simple_plan_sequence_two_atomics())
    tid = c.mark_dispatched("a")
    c.mark_failed("a", "boom")
    # b 仍 PENDING（cursor.done() 為 False）
    s = c.summary()
    assert s["ok"] is False


def test_done_true_when_all_terminal():
    c = PlanCursor(_parallel_two_atomics())
    for nid in ("x", "y"):
        tid = c.mark_dispatched(nid)
        c.accept_action_result(tid, {"task_id": tid, "ok": True})
    assert c.done() is True

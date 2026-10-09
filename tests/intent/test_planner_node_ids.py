"""RecursivePlanner 節點 id 單元測試（C-01）：LLM 給的同層 id 必須轉成全域唯一 id。"""

from __future__ import annotations

import logging

from src.core.intent.planner import RecursivePlanner
from src.core.monitoring.cursor import PlanCursor, find_duplicate_node_ids


class _FakeDecomposer:
    """依 intent 回傳預先準備的拆解結果（模擬 LLM 每一層都從 "1" 開始編號）。"""

    def __init__(self, by_intent: dict[str, dict]):
        self.by_intent = by_intent

    def decompose(self, intent, available_actions):
        return self.by_intent.get(intent)


def _atomic(local_id, intent, action):
    return {"id": local_id, "intent": intent, "action": action, "is_atomic": True,
            "atomic_source": "pre_defined", "scheduled_start": ""}


def _composite(local_id, intent):
    return {"id": local_id, "intent": intent, "action": "", "is_atomic": False,
            "atomic_source": None, "scheduled_start": ""}


def _seq(a, b):
    return {"type": "Sequence", "from_id": a, "to_id": b}


def _planner(by_intent):
    return RecursivePlanner(decomposer=_FakeDecomposer(by_intent), logger=logging.getLogger("test"))


def _ids(node):
    out = [node["id"]]
    for ch in node.get("sub_plans") or []:
        out.extend(_ids(ch))
    return out


def test_nested_ids_are_prefixed_with_parent_id():
    plan = _planner({
        "root intent": {"sub_intents": [_atomic("1", "A", "TaskA()"), _composite("2", "B")],
                        "relationships": [_seq("1", "2")]},
        "B": {"sub_intents": [_atomic("1", "B1", "TaskB1()"), _atomic("2", "B2", "TaskB2()")],
              "relationships": [_seq("1", "2")]},
    }).plan("root intent", {})

    assert _ids(plan) == ["root", "1", "2", "2.1", "2.2"]
    assert find_duplicate_node_ids(plan) == []
    # relationships 跟著改寫成全域 id
    assert plan["execution_logic"] == [_seq("1", "2")]
    assert plan["sub_plans"][1]["execution_logic"] == [_seq("2.1", "2.2")]


def test_nested_plan_executes_every_atomic():
    plan = _planner({
        "root intent": {"sub_intents": [_atomic("1", "A", "TaskA()"), _composite("2", "B")],
                        "relationships": [_seq("1", "2")]},
        "B": {"sub_intents": [_atomic("1", "B1", "TaskB1()"), _atomic("2", "B2", "TaskB2()")],
              "relationships": []},
    }).plan("root intent", {})

    c = PlanCursor(plan)
    executed = []
    while not c.done():
        ready = c.next_ready_atomics()
        assert ready, "cursor stalled before all atomics finished"
        for rec in ready:
            tid = c.mark_dispatched(rec.node_id)
            c.accept_action_result(tid, {"task_id": tid, "ok": True})
            executed.append(rec.node["intent"])
    assert sorted(executed) == ["A", "B1", "B2"]
    assert c.summary()["ok"] is True


def test_duplicate_or_missing_ids_within_one_level_are_made_unique():
    plan = _planner({
        "root intent": {"sub_intents": [
            _atomic("1", "A", "TaskA()"),
            _atomic("1", "A-again", "TaskA2()"),
            {**_atomic("", "no-id", "TaskC()"), "id": None},
        ], "relationships": []},
    }).plan("root intent", {})

    ids = [n["id"] for n in plan["sub_plans"]]
    assert len(set(ids)) == 3
    assert ids[0] == "1"
    assert find_duplicate_node_ids(plan) == []

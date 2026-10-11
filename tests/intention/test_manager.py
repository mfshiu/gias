"""IntentionManager 單元測試（A-01）：優先序、搶占、取消、結束狀態。"""

from __future__ import annotations

import threading
import time

import pytest

from src.core.intention import IntentionManager, IntentionStatus

S = IntentionStatus


class _Runner:
    """可控制的 run_intention：每個意圖走到「執行中」後等待放行或停止要求。"""

    def __init__(self, behaviors=None):
        self.behaviors = behaviors or {}
        self.gates: dict[str, threading.Event] = {}
        self.calls: list[tuple[str, int]] = []
        self.ignore_stop: set[str] = set()

    def release(self, text):
        self.gates.setdefault(text, threading.Event()).set()

    def __call__(self, intention, stop_event, on_planned):
        self.calls.append((intention.text, intention.attempts))
        behavior = self.behaviors.get(intention.text, "ok")
        if behavior == "unresolved":
            return {"plan": {"type": "leaf_unresolved", "reason": "no matching action"},
                    "summary": {"ok": False, "message": "no matching action"}}
        if behavior == "raise":
            raise RuntimeError("boom")
        plan = {"id": "root", "type": "composite",
                "sub_plans": [{"id": "1", "type": "atomic", "task": "T", "params": {"x": intention.text}}]}
        on_planned(plan)
        gate = self.gates.setdefault(intention.text, threading.Event())
        while not gate.wait(0.01):
            if stop_event.is_set() and intention.text not in self.ignore_stop:
                return {"plan": plan, "summary": {"ok": False, "stopped": True}, "stopped": True}
        ok = behavior != "fail"
        return {"plan": plan, "summary": {"ok": ok, "message": "done" if ok else "step failed", "state_counts": {}}}


def _wait(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def _status(manager, intention_id):
    return manager.get(intention_id)["status"]


@pytest.fixture
def setup():
    created = []

    def make(**kw):
        runner = _Runner(kw.pop("behaviors", None))
        events = []
        m = IntentionManager(run_intention=runner, on_status=events.append, **kw)
        created.append(m)
        m.start()
        return m, runner, events

    yield make
    for m in created:
        m.stop(timeout=2)


def test_intention_lifecycle_to_achieved(setup):
    m, runner, events = setup()
    it = m.submit("帶我去 AI 展區", requester="u1")
    _wait(lambda: _status(m, it.intention_id) == "executing")
    runner.release("帶我去 AI 展區")
    assert m.wait_idle(2)
    d = m.get(it.intention_id)
    assert [h["status"] for h in d["history"]] == ["pending", "planning", "executing", "achieved"]
    assert d["plan_steps"] == [{"id": "1", "task": "T", "params": {"x": "帶我去 AI 展區"}}]
    assert d["result"]["ok"] is True
    assert [e["status"] for e in events] == ["pending", "planning", "executing", "achieved"]
    assert m.store.records[it.intention_id]["status"] == "achieved"


def test_queue_order_by_priority_then_arrival(setup):
    m, runner, _ = setup(preemption=False)
    m.submit("A")
    _wait(lambda: runner.calls == [("A", 1)])
    for text, prio in (("B", 1), ("C", 3), ("D", 3)):
        m.submit(text, priority=prio)
    for text in "ACDB":
        runner.release(text)
    assert m.wait_idle(3)
    assert [c[0] for c in runner.calls] == ["A", "C", "D", "B"]


def test_higher_priority_preempts_and_lower_resumes(setup):
    m, runner, _ = setup()
    low = m.submit("low", priority=0)
    _wait(lambda: _status(m, low.intention_id) == "executing")
    high = m.submit("high", priority=5)
    _wait(lambda: _status(m, low.intention_id) == "suspended")
    _wait(lambda: _status(m, high.intention_id) == "executing")
    runner.release("high")
    _wait(lambda: _status(m, low.intention_id) == "executing")
    runner.release("low")
    assert m.wait_idle(3)

    d = m.get(low.intention_id)
    assert d["status"] == "achieved" and d["attempts"] == 2
    assert [h["status"] for h in d["history"]] == [
        "pending", "planning", "executing", "suspended", "planning", "executing", "achieved"]
    assert d["history"][3]["reason"] == f"preempted by {high.intention_id}"
    assert d["history"][4]["reason"] == "resumed after preemption"
    assert runner.calls == [("low", 1), ("high", 1), ("low", 2)]


def test_equal_priority_does_not_preempt(setup):
    m, runner, _ = setup()
    a = m.submit("A", priority=1)
    _wait(lambda: _status(m, a.intention_id) == "executing")
    b = m.submit("B", priority=1)
    time.sleep(0.1)
    assert _status(m, a.intention_id) == "executing" and _status(m, b.intention_id) == "pending"
    runner.release("A")
    runner.release("B")
    assert m.wait_idle(2)


def test_cancel_pending_running_and_finished(setup):
    m, runner, _ = setup(preemption=False)
    running = m.submit("run")
    _wait(lambda: _status(m, running.intention_id) == "executing")
    queued = m.submit("queued")
    assert m.cancel(queued.intention_id, "user changed mind") is True
    assert _status(m, queued.intention_id) == "dropped"
    assert m.cancel(running.intention_id) is True
    assert m.wait_idle(2)
    assert m.get(running.intention_id)["status"] == "dropped"
    assert m.cancel(running.intention_id) is False      # 已結束
    assert m.cancel("no-such-id") is False
    assert [c[0] for c in runner.calls] == ["run"]       # 被取消的排隊意圖從未執行


def test_preempted_then_cancelled_is_dropped(setup):
    m, runner, _ = setup()
    low = m.submit("low")
    _wait(lambda: _status(m, low.intention_id) == "executing")
    with m._cond:   # 同時收到搶占與取消：取消優先
        m.submit("high", priority=9)
        m.cancel(low.intention_id)
    _wait(lambda: _status(m, low.intention_id) == "dropped")
    runner.release("high")
    assert m.wait_idle(2)


@pytest.mark.parametrize("behavior, reason", [
    ("unresolved", "plan unresolved: no matching action"),
    ("raise", "error: boom"),
    ("fail", "step failed"),
])
def test_failures(setup, behavior, reason):
    m, runner, _ = setup(behaviors={"x": behavior})
    it = m.submit("x")
    runner.release("x")
    assert m.wait_idle(2)
    d = m.get(it.intention_id)
    assert (d["status"], d["reason"]) == ("failed", reason)


def test_completion_wins_over_late_cancel(setup):
    m, runner, _ = setup()
    runner.ignore_stop.add("x")
    it = m.submit("x")
    _wait(lambda: _status(m, it.intention_id) == "executing")
    m.cancel(it.intention_id)
    runner.release("x")
    assert m.wait_idle(2)
    assert _status(m, it.intention_id) == "achieved"


def test_stop_interrupts_running_and_queued():
    runner = _Runner()
    m = IntentionManager(run_intention=runner, preemption=False)
    m.start()
    running = m.submit("run")
    _wait(lambda: _status(m, running.intention_id) == "executing")
    queued = m.submit("queued")
    m.stop(timeout=2, reason="shutdown for maintenance")
    assert _status(m, running.intention_id) == "interrupted"
    assert m.get(queued.intention_id)["reason"] == "shutdown for maintenance"


def test_submit_validation_and_wait_idle_timeout(setup):
    m, runner, _ = setup()
    with pytest.raises(ValueError):
        m.submit("   ")
    m.submit("x", intention_id="fixed-id")
    with pytest.raises(ValueError):
        m.submit("y", intention_id="fixed-id")
    assert m.wait_idle(0.1) is False
    runner.release("x")
    assert m.wait_idle(2) is True

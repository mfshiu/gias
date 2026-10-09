"""ExecutionMonitor 單元測試。"""

from __future__ import annotations

import threading
import time

from src.core.monitoring.monitor import ExecutionMonitor


def _fake_parcel(content):
    class _P:
        pass

    p = _P()
    p.content = content
    return p


def test_drain_results_empty():
    m = ExecutionMonitor()
    assert m.drain_results() == []
    assert m.drain_changes() == []


def test_on_action_result_with_dict_payload():
    m = ExecutionMonitor()
    m.bind_task("t-1", "node-x")
    m.on_action_result("info.result", {"task_id": "t-1", "ok": True, "message": "ok"})
    results = m.drain_results()
    assert len(results) == 1
    ar = results[0]
    assert ar.task_id == "t-1"
    assert ar.node_id == "node-x"
    assert ar.ok is True


def test_on_action_result_with_parcel_content():
    m = ExecutionMonitor()
    m.bind_task("t-2", "node-y")
    m.on_action_result("info.result", _fake_parcel({"task_id": "t-2", "ok": False, "error": "boom"}))
    results = m.drain_results()
    assert results[0].ok is False
    assert results[0].error == "boom"


def test_on_action_result_missing_task_id_dropped():
    m = ExecutionMonitor()
    m.on_action_result("info.result", {"ok": True})
    assert m.drain_results() == []


def test_on_env_event_creates_envchange():
    m = ExecutionMonitor()
    m.on_env_event("blackboard.subscriber.x", {
        "topic": "Zone/AI_Tech_Area/CURRENT_STATE",
        "action": "update",
        "new_value": {"status_name": "Crowded"},
    })
    changes = m.drain_changes()
    assert len(changes) == 1
    assert changes[0].topic == "Zone/AI_Tech_Area/CURRENT_STATE"
    assert changes[0].action == "update"


def test_wait_for_signal_returns_true_on_event():
    m = ExecutionMonitor()
    def producer():
        time.sleep(0.05)
        m.on_action_result("info.result", {"task_id": "z", "ok": True})
    t = threading.Thread(target=producer, daemon=True)
    t.start()
    assert m.wait_for_signal(timeout=1.0) is True


def test_wait_for_signal_timeout():
    m = ExecutionMonitor()
    assert m.wait_for_signal(timeout=0.05) is False

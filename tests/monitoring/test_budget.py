"""Budget / BudgetGuard 單元測試。"""

from __future__ import annotations

import time

from src.core.monitoring.budget import Budget, BudgetGuard


def test_replan_budget_default_3():
    g = BudgetGuard()
    assert g.replan_available() is True
    for _ in range(3):
        assert g.consume_replan() is True
    assert g.replan_available() is False
    assert g.consume_replan() is False


def test_replan_budget_custom():
    g = BudgetGuard(Budget(max_replans=1))
    assert g.consume_replan() is True
    assert g.replan_available() is False


def test_retry_budget_per_node():
    g = BudgetGuard(Budget(max_retries_per_node=2))
    assert g.consume_retry("n1") is True
    assert g.consume_retry("n1") is True
    assert g.retry_available("n1") is False
    assert g.consume_retry("n1") is False
    # other node 仍可用
    assert g.consume_retry("n2") is True


def test_deadline_reached_when_elapsed():
    g = BudgetGuard(Budget(deadline_sec=0.001))
    time.sleep(0.01)
    assert g.deadline_reached() is True
    assert g.exhausted() is True


def test_deadline_none_means_never_reached():
    g = BudgetGuard(Budget(deadline_sec=None))
    assert g.deadline_reached() is False


def test_snapshot_has_counters():
    g = BudgetGuard(Budget(max_replans=2, max_retries_per_node=1))
    g.consume_replan()
    g.consume_retry("a")
    snap = g.snapshot()
    assert snap["replans_used"] == 1
    assert snap["retries_by_node"] == {"a": 1}


def test_used_up_replans_and_retries_do_not_exhaust_budget():
    """exhausted() 是監測迴圈的結束條件：配額用完只是不能再 replan / retry，
    最後一次 replan 換上的節點仍要執行，所以不可視為耗盡（C-09）。"""
    g = BudgetGuard(Budget(max_replans=1, max_retries_per_node=1, deadline_sec=None))
    g.consume_replan()
    g.consume_retry("a")
    assert g.replan_available() is False
    assert g.retry_available("a") is False
    assert g.exhausted() is False

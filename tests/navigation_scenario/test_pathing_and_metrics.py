"""
pathing.py 與 metrics.py 單元測試

對 pathing：用一個手工構造的 GraphSnapshot 測試 Dijkstra 行為，
            包含 avoid_blocked / avoid_crowded / avoid_closed 邏輯。
對 metrics：CaseMetrics 派生指標、AggregateMetrics、format_summary_table。
"""

from __future__ import annotations

import pytest

from navigation_scenario.metrics import (
    AggregateMetrics,
    CaseMetrics,
    aggregate,
    format_summary_table,
    group_by_category_and_pct,
)
from navigation_scenario.pathing import (
    GraphSnapshot,
    multi_segment_path,
    shortest_path,
)


# =============================================================================
# 建一個小型測試圖
#
#         A ---10--- B ---10--- C
#                    |
#                   30
#                    |
#         D ---10--- E ---10--- F
#
# A, C 在 ZoneN（北區）；D, F 在 ZoneS（南區）；B, E 為樞紐節點
# =============================================================================
def _build_snap() -> GraphSnapshot:
    snap = GraphSnapshot()
    snap.nodes = {
        "A": {"label": "POI", "zone": "ZoneN"},
        "B": {"label": "POI", "zone": "Hub"},
        "C": {"label": "POI", "zone": "ZoneN"},
        "D": {"label": "POI", "zone": "ZoneS"},
        "E": {"label": "POI", "zone": "Hub"},
        "F": {"label": "POI", "zone": "ZoneS"},
    }
    edges = {
        ("A", "B"): 10, ("B", "A"): 10,
        ("B", "C"): 10, ("C", "B"): 10,
        ("B", "E"): 30, ("E", "B"): 30,
        ("D", "E"): 10, ("E", "D"): 10,
        ("E", "F"): 10, ("F", "E"): 10,
    }
    for (a, b), dist in edges.items():
        snap.edges[(a, b)] = {"distance": float(dist), "blocked": False}
    snap.zone_states = {"ZoneN": "Normal", "ZoneS": "Normal", "Hub": "Normal"}
    return snap


# =============================================================================
# pathing
# =============================================================================
class TestShortestPath:
    def test_simple_path(self):
        snap = _build_snap()
        result = shortest_path(snap, "A", "F")
        assert result.reachable
        assert result.nodes == ["A", "B", "E", "F"]
        assert result.distance == 50.0

    def test_unreachable_endpoint(self):
        snap = _build_snap()
        # 拔掉所有 E 的連線
        snap.edges = {
            k: v for k, v in snap.edges.items()
            if "E" not in k and (k[0] != "E" and k[1] != "E")
        }
        result = shortest_path(snap, "A", "F")
        assert not result.reachable

    def test_avoid_blocked_edge(self):
        snap = _build_snap()
        snap.edges[("B", "E")]["blocked"] = True
        snap.edges[("E", "B")]["blocked"] = True
        result = shortest_path(snap, "A", "F")
        # 路徑被切斷，應不可達
        assert not result.reachable

    def test_avoid_crowded_zone(self):
        snap = _build_snap()
        snap.zone_states["Hub"] = "Crowded"
        # 沒指定 avoid_crowded → 仍可走
        r1 = shortest_path(snap, "A", "F")
        assert r1.reachable

        # 指定 avoid_crowded → B 和 E 都在 Hub，被排除（但起點 A 在 ZoneN，
        # 終點 F 在 ZoneS，唯一路徑經過 Hub → 不可達）
        r2 = shortest_path(snap, "A", "F", constraints=["avoid_crowded"])
        assert not r2.reachable

    def test_avoid_closed_endpoint_still_allowed(self):
        """終點所在 zone 為 Closed 時仍允許前往（讓 LocateExhibit 能達成）。"""
        snap = _build_snap()
        snap.zone_states["ZoneS"] = "Closed"
        # 走到 F 的「最後一步」需進入 Closed zone；終點放行
        result = shortest_path(snap, "A", "F", constraints=["avoid_closed"])
        assert result.reachable
        assert result.nodes[-1] == "F"


class TestMultiSegment:
    def test_two_segments(self):
        snap = _build_snap()
        plan = multi_segment_path(snap, "A", ["C", "F"])
        assert plan.reachable
        # A→B→C = 20，C→B→E→F = 50，合計 70
        assert plan.total_distance == 70.0
        assert plan.segments[0].nodes == ["A", "B", "C"]
        assert plan.segments[1].nodes == ["C", "B", "E", "F"]

    def test_one_segment_unreachable_breaks_chain(self):
        snap = _build_snap()
        snap.edges[("B", "C")]["blocked"] = True
        snap.edges[("C", "B")]["blocked"] = True
        plan = multi_segment_path(snap, "A", ["C", "F"])
        assert not plan.reachable
        assert len(plan.segments) == 1   # 第一段就斷
        assert not plan.segments[0].reachable

    def test_flat_nodes_dedup_joint(self):
        snap = _build_snap()
        plan = multi_segment_path(snap, "A", ["B", "F"])
        flat = plan.flat_nodes()
        # B 不應重複（接點）
        assert flat == ["A", "B", "E", "F"]


# =============================================================================
# metrics
# =============================================================================
def _make_case_metrics(
    *,
    case_id="x",
    category="single_target",
    pct=30,
    event="crowd_congestion",
    task_success=True,
    intention_kept=True,
    baseline=100.0,
    actual=120.0,
    replan_required=False,
    replan_attempted=False,
    replan_success=False,
    completion_time=120.0,
) -> CaseMetrics:
    return CaseMetrics(
        case_id=case_id,
        category=category,
        injection_pct=pct,
        event_kind=event,
        task_success=task_success,
        targets_reached=1 if task_success else 0,
        targets_total=1,
        intention_kept=intention_kept,
        baseline_distance=baseline,
        actual_distance=actual,
        replan_required=replan_required,
        replan_attempted=replan_attempted,
        replan_success=replan_success,
        completion_time_sec=completion_time,
    )


class TestCaseMetricsDerived:
    def test_tsr(self):
        ok = _make_case_metrics(task_success=True)
        bad = _make_case_metrics(task_success=False, intention_kept=False)
        assert ok.tsr == 1.0
        assert bad.tsr == 0.0

    def test_isr_requires_intention(self):
        ok = _make_case_metrics(task_success=True, intention_kept=True)
        partial = _make_case_metrics(task_success=True, intention_kept=False)
        assert ok.isr == 1.0
        assert partial.isr == 0.0

    def test_rsr_when_replan_not_required(self):
        m = _make_case_metrics(replan_required=False, task_success=True)
        assert m.rsr == 1.0   # n/a → 視為 1

    def test_rsr_when_replan_required(self):
        ok = _make_case_metrics(
            replan_required=True, replan_attempted=True,
            replan_success=True, task_success=True,
        )
        bad = _make_case_metrics(
            replan_required=True, replan_attempted=True,
            replan_success=False, task_success=False,
            intention_kept=False,
        )
        assert ok.rsr == 1.0
        assert bad.rsr == 0.0

    def test_path_efficiency(self):
        m = _make_case_metrics(baseline=100, actual=120)
        assert abs(m.path_efficiency - 100.0/120.0) < 1e-6

    def test_path_efficiency_clipped(self):
        # actual < baseline 理論不應發生，但若發生 PE 應 clip 到 1
        m = _make_case_metrics(baseline=120, actual=100)
        assert m.path_efficiency == 1.0


class TestAggregate:
    def test_empty(self):
        agg = aggregate([])
        assert agg.n_cases == 0
        assert agg.tsr == 0.0

    def test_basic_aggregate(self):
        cs = [
            _make_case_metrics(case_id="a", task_success=True, intention_kept=True),
            _make_case_metrics(case_id="b", task_success=True, intention_kept=False),
            _make_case_metrics(case_id="c", task_success=False, intention_kept=False),
        ]
        agg = aggregate(cs)
        assert agg.n_cases == 3
        assert abs(agg.tsr - 2/3) < 1e-6
        assert abs(agg.isr - 1/3) < 1e-6

    def test_rsr_only_counts_required(self):
        cs = [
            _make_case_metrics(case_id="a", replan_required=False, task_success=True),
            _make_case_metrics(
                case_id="b", replan_required=True, replan_attempted=True,
                replan_success=True, task_success=True,
            ),
            _make_case_metrics(
                case_id="c", replan_required=True, replan_attempted=True,
                replan_success=False, task_success=False, intention_kept=False,
            ),
        ]
        agg = aggregate(cs)
        # replan 需求 2 個，成功 1 個 → 50%
        assert agg.replan_required_cases == 2
        assert agg.replan_success_cases == 1
        assert abs(agg.rsr - 0.5) < 1e-6


class TestSummaryTable:
    def test_table_format(self):
        cs = [
            _make_case_metrics(
                case_id="s30a", category="single_target", pct=30,
                task_success=True, intention_kept=True,
            ),
            _make_case_metrics(
                case_id="s60a", category="single_target", pct=60,
                task_success=True, intention_kept=True,
            ),
            _make_case_metrics(
                case_id="c30a", category="constrained", pct=30,
                task_success=True, intention_kept=True,
            ),
            _make_case_metrics(
                case_id="m30a", category="multi_step", pct=30,
                task_success=False, intention_kept=False,
            ),
        ]
        grouped = group_by_category_and_pct(cs)
        text = format_summary_table(grouped)
        assert "Single-Goal" in text
        assert "Constraint-Based" in text
        assert "Multi-Step" in text
        # 應該每類別出現一列
        assert text.count("\n") >= 4

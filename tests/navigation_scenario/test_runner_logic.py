"""
Navigation Scenario runner.py 核心邏輯測試

策略：把 `load_snapshot` 與 `_inject_via_mqtt` 用 monkeypatch 換成模擬版，
這樣不需要 Neo4j 或 MQTT broker 也能驗證 runner 的決策邏輯：
  - 事件注入時機（30% / 60%）
  - 注入後 replan 行為
  - PE / ISR / RSR / TSR 計算
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from navigation_scenario.config import (
    EVENT_KIND_CLOSURE,
    EVENT_KIND_CROWD,
    EVENT_KIND_DETOUR,
)
from navigation_scenario.pathing import GraphSnapshot
from navigation_scenario.test_cases import DynamicEvent, Target, TestCase
from navigation_scenario import runner as runner_module


# =============================================================================
# 構造一個小型 GraphSnapshot（北/南雙路徑）
# =============================================================================
def _build_test_snapshot(
    *,
    block_north: bool = False,
    crowded_zones: tuple[str, ...] = (),
    closed_zones: tuple[str, ...] = (),
) -> GraphSnapshot:
    r"""
    P_Entrance --10-- N1 --10-- N2 --10-- TARGET     (北路徑，總 30)
        \                                     /
         \--10-- S1 --10-- S2 --15-- S3 --10-/      (南路徑，總 45)
    """
    snap = GraphSnapshot()
    snap.nodes = {
        "P_Entrance": {"label": "POI", "zone": "Main_Hall"},
        "N1": {"label": "POI", "zone": "AI_Tech_Area"},
        "N2": {"label": "POI", "zone": "AI_Tech_Area"},
        "TARGET": {"label": "Booth", "zone": "AI_Tech_Area"},
        "S1": {"label": "POI", "zone": "Gaming_Area"},
        "S2": {"label": "POI", "zone": "Gaming_Area"},
        "S3": {"label": "POI", "zone": "VR_Area"},
    }
    raw_edges = [
        ("P_Entrance", "N1", 10),
        ("N1", "N2", 10),
        ("N2", "TARGET", 10),
        ("P_Entrance", "S1", 10),
        ("S1", "S2", 10),
        ("S2", "S3", 15),
        ("S3", "TARGET", 10),
    ]
    for a, b, d in raw_edges:
        blocked = False
        if block_north and ((a, b) in (("N1", "N2"), ("N2", "TARGET")) or (b, a) in (("N1", "N2"), ("N2", "TARGET"))):
            blocked = True
        snap.edges[(a, b)] = {"distance": float(d), "blocked": blocked}
        snap.edges[(b, a)] = {"distance": float(d), "blocked": blocked}

    snap.zone_states = {
        "Main_Hall": "Normal",
        "AI_Tech_Area": "Normal",
        "Gaming_Area": "Normal",
        "VR_Area": "Normal",
    }
    for z in crowded_zones:
        snap.zone_states[z] = "Crowded"
    for z in closed_zones:
        snap.zone_states[z] = "Closed"
    return snap


def _make_case(
    *,
    case_id="t",
    category="single_target",
    constraints: tuple[str, ...] = (),
    event_kind=EVENT_KIND_CLOSURE,
    event_target="AI_Tech_Area",
    injection_pct=30,
    expected_replan=True,
) -> TestCase:
    return TestCase(
        case_id=case_id,
        category=category,
        request="test",
        start="P_Entrance",
        targets=(Target(id="TARGET", kind="booth", label="Target"),),
        constraints=constraints,
        event=DynamicEvent(
            kind=event_kind, target=event_target,
            injection_pct=injection_pct, ttl_samples=3,
        ),
        expected_replan=expected_replan,
    )


class _FakeInjector:
    """
    模擬注入器：
      - 第一次 inject 被呼叫時，把全域旗標翻起來，下一次 load_snapshot 取出有事件影響的快照。
    """
    def __init__(self):
        self.injected_events: list[dict] = []
        self.recovered: list[dict] = []

    def disconnect(self):
        pass

    # runner 用到的方法
    def inject_crowd_congestion(self, zone, **kw):
        self.injected_events.append({"kind": "crowd_congestion", "target": zone})
        return self.injected_events[-1]

    def inject_area_closure(self, zone, **kw):
        self.injected_events.append({"kind": "area_closure", "target": zone})
        return self.injected_events[-1]

    def inject_route_detour(self, a, b, **kw):
        self.injected_events.append({"kind": "route_detour", "target": {"from": a, "to": b}})
        return self.injected_events[-1]

    def inject_event(self, kind, target, **kw):
        # for recovery
        self.recovered.append({"kind": kind, "target": target})
        return self.recovered[-1]

    def inject_route_clear(self, a, b, **kw):
        self.recovered.append({"kind": "route_clear", "from": a, "to": b})
        return self.recovered[-1]


# =============================================================================
# 共用 fixture：patch load_snapshot & sleep
# =============================================================================
@pytest.fixture
def patch_runner(monkeypatch):
    state = {"before": None, "after": None}
    call_count = {"n": 0}

    def fake_load_snapshot():
        # 第 1 次取 baseline；之後取 after-injection
        if call_count["n"] == 0:
            call_count["n"] += 1
            return state["before"]
        return state["after"] if state["after"] is not None else state["before"]

    monkeypatch.setattr(runner_module, "load_snapshot", fake_load_snapshot)
    monkeypatch.setattr(runner_module.time, "sleep", lambda *_: None)
    return state, call_count


# =============================================================================
# 測試
# =============================================================================
class TestRunnerHappyPath:
    def test_no_injection_effect_succeeds(self, patch_runner):
        state, _ = patch_runner
        state["before"] = _build_test_snapshot()
        # 沒有變化的快照 → 注入後 snapshot 仍可走原路
        state["after"] = _build_test_snapshot()

        injector = _FakeInjector()
        case = _make_case(event_kind=EVENT_KIND_CROWD, expected_replan=False)
        result = runner_module.run_case(case, injector=injector)

        m = result.metrics
        assert m.task_success
        assert m.tsr == 1.0
        assert m.isr == 1.0
        assert m.rsr == 1.0   # 不需要 replan
        assert m.path_efficiency > 0.9
        assert m.targets_reached == 1


class TestRunnerReplan:
    def test_area_closure_triggers_replan_via_south_path(self, patch_runner):
        state, _ = patch_runner
        state["before"] = _build_test_snapshot()
        # 注入後北側 AI_Tech_Area 被關閉
        state["after"] = _build_test_snapshot(closed_zones=("AI_Tech_Area",))

        injector = _FakeInjector()
        case = _make_case(
            event_kind=EVENT_KIND_CLOSURE,
            event_target="AI_Tech_Area",
            constraints=("avoid_closed",),
            expected_replan=True,
            injection_pct=30,
        )
        result = runner_module.run_case(case, injector=injector)
        m = result.metrics

        # AI_Tech_Area 是終點 zone，pathing 規則允許進入 endpoint zone，
        # 所以 replan 仍然可以走北路徑（雖然 zone 被關），任務應該成功
        assert m.task_success, f"task should succeed, notes={m.notes}"
        # 路徑可能會繞遠，PE 不會是滿分但 > 0
        assert 0.0 < m.path_efficiency <= 1.0

    def test_route_detour_blocks_north_path(self, patch_runner):
        state, _ = patch_runner
        state["before"] = _build_test_snapshot()
        state["after"] = _build_test_snapshot(block_north=True)

        injector = _FakeInjector()
        case = _make_case(
            event_kind=EVENT_KIND_DETOUR,
            event_target={"from": "N1", "to": "N2"},
            constraints=("avoid_blocked",),
            expected_replan=True,
        )
        result = runner_module.run_case(case, injector=injector)
        m = result.metrics

        # 北路徑被封 → 必須改走南路徑
        assert m.task_success
        # 實際距離應 > baseline（30 m）→ PE < 1
        assert m.actual_distance > m.baseline_distance
        assert m.path_efficiency < 1.0
        assert m.replan_attempted


class TestRunnerAggregateMetricsFromSimulation:
    def test_multiple_cases_aggregate(self, patch_runner, tmp_path):
        from navigation_scenario.metrics import (
            aggregate, group_by_category_and_pct,
        )

        state, _ = patch_runner
        state["before"] = _build_test_snapshot()
        state["after"] = _build_test_snapshot(block_north=True)

        injector = _FakeInjector()

        # 跑 3 個案例（重複 detour 案例，注入時機不同）
        cases = [
            _make_case(
                case_id=f"sim_{i}", category="single_target",
                event_kind=EVENT_KIND_DETOUR,
                event_target={"from": "N1", "to": "N2"},
                constraints=("avoid_blocked",),
                injection_pct=pct,
            )
            for i, pct in enumerate([30, 30, 60])
        ]
        results = [runner_module.run_case(c, injector=injector) for c in cases]
        metrics = [r.metrics for r in results]
        agg = aggregate(metrics)
        assert agg.n_cases == 3
        # 路徑都被封 + 都成功 replan → RSR 100%
        assert agg.rsr == 1.0


class TestRecovery:
    def test_recovery_event_dispatched(self, patch_runner):
        state, _ = patch_runner
        state["before"] = _build_test_snapshot()
        state["after"] = _build_test_snapshot(block_north=True)

        injector = _FakeInjector()
        case = _make_case(
            event_kind=EVENT_KIND_DETOUR,
            event_target={"from": "N1", "to": "N2"},
            constraints=("avoid_blocked",),
        )
        runner_module.run_case(case, injector=injector, auto_recover=True)
        # 結束時應該送過 route_clear（_FakeInjector 用 .recovered 紀錄）
        assert any(r["kind"] == "route_clear" for r in injector.recovered)

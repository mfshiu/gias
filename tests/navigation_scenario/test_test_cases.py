"""
test_cases.py 單元測試：確認 60 個案例的結構完整性。
"""

from __future__ import annotations

import pytest

from navigation_scenario.config import (
    BOOTHS,
    EVENT_KIND_CLOSURE,
    EVENT_KIND_CROWD,
    EVENT_KIND_DETOUR,
    POIS,
    ZONES,
)
from navigation_scenario.test_cases import (
    DynamicEvent,
    TestCase,
    all_cases,
    case_by_id,
    cases_by_category,
    filter_cases,
    summary,
)


VALID_ZONES = set(ZONES)
VALID_BOOTH_IDS = {b["id"] for b in BOOTHS}
VALID_POI_IDS = {p["id"] for p in POIS}
VALID_NODE_IDS = VALID_BOOTH_IDS | VALID_POI_IDS


# --------------------------------------------------------------------
# 數量與分佈
# --------------------------------------------------------------------
class TestCounts:
    def test_total_is_60(self):
        assert len(all_cases()) == 60

    def test_20_per_category(self):
        for cat in ("single_target", "constrained", "multi_step"):
            assert len(cases_by_category(cat)) == 20

    def test_balanced_injection_pcts(self):
        s = summary()
        assert s["inject_30pct"] == 30
        assert s["inject_60pct"] == 30

    def test_all_three_event_kinds_present(self):
        s = summary()
        assert s["event_crowd"] > 0
        assert s["event_closure"] > 0
        assert s["event_detour"] > 0


# --------------------------------------------------------------------
# 案例結構檢查
# --------------------------------------------------------------------
class TestCaseStructure:
    @pytest.mark.parametrize("case", all_cases(), ids=lambda c: c.case_id)
    def test_case_id_uniqueness(self, case):
        # 不需要重複檢查全部，pytest 會逐個跑
        assert case.case_id

    def test_case_ids_globally_unique(self):
        ids = [c.case_id for c in all_cases()]
        assert len(set(ids)) == len(ids)

    @pytest.mark.parametrize("case", all_cases(), ids=lambda c: c.case_id)
    def test_targets_valid(self, case):
        assert len(case.targets) >= 1
        for t in case.targets:
            if t.kind == "zone":
                assert t.id in VALID_ZONES, f"unknown zone {t.id}"
            elif t.kind == "booth":
                assert t.id in VALID_BOOTH_IDS, f"unknown booth {t.id}"
            elif t.kind == "poi":
                assert t.id in VALID_POI_IDS, f"unknown poi {t.id}"
            else:
                pytest.fail(f"unknown kind {t.kind}")

    @pytest.mark.parametrize("case", all_cases(), ids=lambda c: c.case_id)
    def test_injection_pct(self, case):
        assert case.event.injection_pct in (30, 60)

    @pytest.mark.parametrize("case", all_cases(), ids=lambda c: c.case_id)
    def test_event_kind_valid(self, case):
        assert case.event.kind in (
            EVENT_KIND_CROWD, EVENT_KIND_CLOSURE, EVENT_KIND_DETOUR,
        )

    @pytest.mark.parametrize("case", all_cases(), ids=lambda c: c.case_id)
    def test_event_target_resolves(self, case):
        kind = case.event.kind
        target = case.event.target
        if kind in (EVENT_KIND_CROWD, EVENT_KIND_CLOSURE):
            assert target in VALID_ZONES, f"event target zone {target} unknown"
        elif kind == EVENT_KIND_DETOUR:
            assert isinstance(target, dict), "detour target must be {from,to}"
            assert "from" in target and "to" in target
            assert target["from"] in VALID_NODE_IDS
            assert target["to"] in VALID_NODE_IDS

    @pytest.mark.parametrize("case", all_cases(), ids=lambda c: c.case_id)
    def test_start_node(self, case):
        assert case.start == "P_Entrance"

    @pytest.mark.parametrize("case", all_cases(), ids=lambda c: c.case_id)
    def test_expected_replan_consistent(self, case):
        # closure / detour 一定需要 replan
        if case.event.kind in (EVENT_KIND_CLOSURE, EVENT_KIND_DETOUR):
            assert case.expected_replan
        # crowd_congestion 只在 avoid_crowded 時才需要
        elif case.event.kind == EVENT_KIND_CROWD:
            need = "avoid_crowded" in case.constraints
            assert case.expected_replan == need


# --------------------------------------------------------------------
# 篩選 API
# --------------------------------------------------------------------
class TestFilters:
    def test_filter_by_category(self):
        cs = filter_cases(category="single_target")
        assert len(cs) == 20
        assert all(c.category == "single_target" for c in cs)

    def test_filter_by_pct(self):
        cs = filter_cases(injection_pct=30)
        assert len(cs) == 30

    def test_filter_combination(self):
        cs = filter_cases(category="constrained", injection_pct=60)
        assert len(cs) == 10

    def test_case_by_id_roundtrip(self):
        for c in all_cases():
            assert case_by_id(c.case_id) is c or case_by_id(c.case_id) == c

    def test_case_by_id_not_found(self):
        assert case_by_id("nonexistent_id") is None

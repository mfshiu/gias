"""live 模式指標計算單元測試：從機器人實際軌跡算出 CaseMetrics。"""

from __future__ import annotations

from navigation_scenario.live.evaluation import (
    constraint_violations,
    evaluate_live_case,
    ia_adaptations,
    plan_steps,
    reached_targets,
)
from navigation_scenario.live.guide_agent import WalkStep
from navigation_scenario.pathing import multi_segment_path, snapshot_from_config
from navigation_scenario.test_cases import DynamicEvent, Target, TestCase


def _case(targets, constraints=(), *, expected_replan=True, pct=30) -> TestCase:
    return TestCase(
        case_id="t_01_30", category="multi_step" if len(targets) > 1 else "single_target",
        request="test", start="P_Entrance", targets=tuple(targets), constraints=tuple(constraints),
        event=DynamicEvent(kind="crowd_congestion", target="AI_Tech_Area", injection_pct=pct),
        expected_replan=expected_replan,
    )


def _walk(nodes, *, zones, states=None, rerouted=(), t0=100.0):
    snap = snapshot_from_config()
    steps = []
    for i, (a, b) in enumerate(zip(nodes, nodes[1:])):
        steps.append(WalkStep(
            task="LocateExhibit", task_id="t", from_node=a, to_node=b,
            distance=snap.edges[(a, b)]["distance"], zone=zones.get(b),
            zone_state=(states or {}).get(b, "Normal"), at=t0 + i, rerouted=i in rerouted,
        ))
    return steps


_ZONES = {"P_Info": "Main_Hall", "P_North_Hub": "Main_Hall", "B_AI1": "AI_Tech_Area", "B_RB1": "Robotics_Area",
          "B_RB2": "Robotics_Area", "B_SU1": "Startup_Area", "P_South_Hub": "Main_Hall", "B_GM1": "Gaming_Area"}


def test_reached_targets_in_order_with_zone_targets():
    case = _case([Target("Robotics_Area", "zone", "R"), Target("AI_Tech_Area", "zone", "A")])
    traj = _walk(["P_Entrance", "P_Info", "B_RB1", "B_AI1"], zones=_ZONES)
    assert reached_targets(case, "Main_Hall", traj) == [1, 2]
    # 順序顛倒時不算到達第二個目標
    traj_rev = _walk(["P_Entrance", "P_Info", "P_North_Hub", "B_AI1", "B_RB1"], zones=_ZONES)
    assert reached_targets(case, "Main_Hall", traj_rev) == [3]


def test_constraint_violation_ignores_target_zone():
    case = _case([Target("B_AI1", "booth", "AI")], constraints=("avoid_crowded",))
    traj = _walk(["P_Entrance", "P_Info", "P_North_Hub", "B_AI1"], zones=_ZONES,
                 states={"P_North_Hub": "Crowded", "B_AI1": "Crowded"})
    assert [v["to_node"] for v in constraint_violations(case, traj)] == ["P_North_Hub"]
    assert constraint_violations(_case([Target("B_AI1", "booth", "AI")]), traj) == []


def test_ia_adaptations_and_plan_steps():
    summary = {"replan_log": [{"kind": "replace_subtree"}],
               "results": [{"attempts": 2}, {"attempts": 1}, {"attempts": 0}]}
    assert ia_adaptations(summary) == {"replans": 1, "redispatches": 1}
    plan = {"id": "root", "sub_plans": [
        {"id": "1", "type": "atomic", "task": "SuggestRoute", "params": {"destination": "VR_Area"}},
        {"id": "2", "type": "composite", "sub_plans": [{"id": "2.1", "is_atomic": True, "task": "LocateExhibit"}]},
    ]}
    assert [s["task"] for s in plan_steps(plan)] == ["SuggestRoute", "LocateExhibit"]


def _evaluate(case, traj, *, outcome=None, injection=None, timed_out=False):
    snap = snapshot_from_config()
    targets = [t.id if t.kind != "zone" else "B_AI1" for t in case.targets]
    baseline = multi_segment_path(snap, case.start, targets)
    return evaluate_live_case(
        case, target_nodes=targets, baseline_plan=baseline, start_zone="Main_Hall", trajectory=traj,
        outcome=outcome or {"plan": {"type": "composite"}, "summary": {"ok": True, "results": []}},
        injection=injection, timed_out=timed_out, wall_sec=12.5, seconds_per_meter=0.1,
    )


def test_detour_case_metrics():
    case = _case([Target("B_GM1", "booth", "GM")], constraints=("avoid_blocked",))
    # baseline：P_Entrance → P_Info → P_South_Hub → B_GM1 = 48 m；實際繞經 B_SU1 = 68 m
    traj = _walk(["P_Entrance", "P_Info", "B_SU1", "P_South_Hub", "B_GM1"], zones=_ZONES, rerouted={1})
    m = _evaluate(case, traj, injection={"at": 100.5, "targets_reached": 0, "progress_pct": 33.3})
    assert m.task_success is True
    assert m.intention_kept is True
    assert m.baseline_distance == 48
    assert m.actual_distance == 68
    assert round(m.path_efficiency, 3) == round(48 / 68, 3)
    assert m.replan_required is True
    assert m.replan_attempted is True and m.replan_success is True
    assert m.extra["executor_reroutes_after_injection"] == 1
    assert m.extra["walk_time_sec"] == 6.8
    assert m.completion_time_sec == 12.5


def test_multi_target_segments_split_at_each_target():
    case = _case([Target("B_RB1", "booth", "R"), Target("B_AI1", "booth", "A")])
    traj = _walk(["P_Entrance", "P_Info", "B_RB1", "B_AI1"], zones=_ZONES)
    m = _evaluate(case, traj)
    assert m.segment_actuals == [35, 18]
    assert m.task_success is True


def test_failure_notes_and_flags():
    case = _case([Target("B_GM1", "booth", "GM")], constraints=("avoid_crowded",))
    traj = _walk(["P_Entrance", "P_Info"], zones=_ZONES)
    outcome = {"plan": {"type": "composite"},
               "summary": {"ok": False, "results": [
                   {"id": "1", "task": "LocateExhibit", "state": "failed", "error": "no passable route", "attempts": 2}],
                   "replan_log": []}}
    m = _evaluate(case, traj, outcome=outcome, injection=None, timed_out=False)
    assert m.task_success is False and m.intention_kept is False
    assert m.replan_required is False and m.replan_attempted is False
    assert "no passable route" in m.notes
    assert m.extra["ia_redispatches"] == 1

    unresolved = _evaluate(case, [], outcome={"plan": {"type": "leaf_unresolved", "reason": "scope gate"}, "summary": {}})
    assert "plan unresolved: scope gate" in unresolved.notes
    assert unresolved.actual_distance == float("inf")

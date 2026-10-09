"""GuideAgent 單元測試：在 Blackboard 圖（記憶體快照）上實際移動。"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from src.agents._executor_utils import CancelToken
from navigation_scenario.live.guide_agent import (
    GuideAgent,
    RouteUnavailable,
    UnknownDestination,
    destinations_for,
    resolve_destination,
)
from navigation_scenario.pathing import snapshot_from_config

_EMPTY_BROKER = {"broker": {"broker_name": "mqtt01", "mqtt01": {"broker_type": "empty"}}}


class _Parcel:
    def __init__(self, content):
        self.content = content


def _guide(snap, **kw) -> GuideAgent:
    return GuideAgent(_EMPTY_BROKER, snapshot_loader=lambda: snap, seconds_per_meter=0.0, **kw)


def _path(guide: GuideAgent) -> list[str]:
    _, _, traj = guide.snapshot_state()
    return [traj[0].from_node] + [s.to_node for s in traj] if traj else [guide.position]


# ----------------------------------------------------------------------
# 目的地解析
# ----------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("B_GM1", "B_GM1"),                 # 節點 id
    ("AI_Tech_Area", "B_AI1"),          # zone id → 該區最近的節點
    ("AI 展區", "B_AI1"),               # 中文名稱
    ("AI展區", "B_AI1"),
    ("TechCorp AI", "B_AI1"),           # 攤位名稱
    ("咖啡廳", "P_Cafe"),
    ("最近的洗手間", "P_Restroom_S"),   # 從入口出發，南側洗手間較近（52m vs 72m）
    ("北側洗手間", "P_Restroom_N"),
    ("facility:exit", "P_Exit"),
])
def test_resolve_destination(raw, expected):
    assert resolve_destination(snapshot_from_config(), raw, start="P_Entrance") == expected


@pytest.mark.parametrize("raw", [None, "", "火星展區"])
def test_resolve_destination_rejects_unknown(raw):
    with pytest.raises(UnknownDestination):
        resolve_destination(snapshot_from_config(), raw, start="P_Entrance")


def test_destinations_for_each_task():
    assert destinations_for("LocateExhibit", {"target_name": "B_GM1"}) == ["B_GM1"]
    assert destinations_for("SuggestRoute", {"destination": "VR_Area", "waypoints": ["Robotics_Area"]}) == [
        "Robotics_Area", "VR_Area"]
    assert destinations_for("SuggestRoute", {"destination": "VR_Area", "waypoints": "[Robotics_Area, IoT_Area]"}) == [
        "Robotics_Area", "IoT_Area", "VR_Area"]
    assert destinations_for("ReplanRoute", {"remaining_goals": ["VR_Area"]}) == ["VR_Area"]
    assert destinations_for("LocateFacility", {"facility_type": "restroom"}) == ["facility:restroom"]
    with pytest.raises(UnknownDestination):
        destinations_for("LocateExhibit", {"target_type": "booth"})


# ----------------------------------------------------------------------
# 移動
# ----------------------------------------------------------------------
def test_locate_exhibit_walks_shortest_path():
    guide = _guide(snapshot_from_config())
    message, cancelled = guide.execute_task("LocateExhibit", {"target_name": "AI_Tech_Area"}, task_id="t1")
    assert cancelled is False
    assert _path(guide) == ["P_Entrance", "P_Info", "P_North_Hub", "B_AI1"]
    assert guide.position == "B_AI1"
    assert guide.walked_distance == 42
    assert "B_AI1" in message


def test_position_carries_over_between_tasks():
    guide = _guide(snapshot_from_config())
    guide.execute_task("LocateExhibit", {"target_name": "B_RB1"})
    guide.execute_task("LocateExhibit", {"target_name": "B_RB2", "current_location": "P_Entrance"})
    # 第二個任務從 B_RB1 出發，不理會 LLM 給的 current_location
    assert _path(guide) == ["P_Entrance", "P_Info", "B_RB1", "B_RB2"]


def test_reroutes_when_passage_blocked_mid_route():
    snap = snapshot_from_config()
    guide = _guide(snap)

    def block_after_first_step(step):
        if step.to_node == "P_Info":
            snap.edges[("P_Info", "P_South_Hub")]["blocked"] = True
            snap.edges[("P_South_Hub", "P_Info")]["blocked"] = True

    guide.step_listener = block_after_first_step
    guide.execute_task("LocateExhibit", {"target_name": "B_GM1"})
    assert _path(guide) == ["P_Entrance", "P_Info", "B_SU1", "P_South_Hub", "B_GM1"]
    _, _, traj = guide.snapshot_state()
    assert traj[1].rerouted is True


def test_closed_zone_is_never_passed_through():
    snap = snapshot_from_config()
    snap.zone_states["Gaming_Area"] = "Closed"
    guide = _guide(snap)
    guide.execute_task("LocateExhibit", {"target_name": "B_VR1"})
    _, _, traj = guide.snapshot_state()
    assert all(s.zone != "Gaming_Area" for s in traj)
    assert guide.position == "B_VR1"


def test_avoid_crowded_preference_persists_within_session():
    snap = snapshot_from_config()
    snap.zone_states["Gaming_Area"] = "Crowded"
    guide = _guide(snap)
    guide.execute_task("SuggestRoute", {"destination": "B_IoT1", "avoid_crowded": True})
    guide.execute_task("LocateExhibit", {"target_name": "B_VR1"})   # 沒有帶 avoid_crowded
    _, _, traj = guide.snapshot_state()
    assert all(s.zone != "Gaming_Area" for s in traj)
    guide.reset("P_Entrance")
    assert guide.preferences == set()


def test_avoid_crowded_is_relaxed_when_no_alternative():
    snap = snapshot_from_config()
    # 入口的兩個鄰居（P_Info、P_Bio1）所在區域都擁擠：不放寬就出不去
    snap.zone_states["Main_Hall"] = "Crowded"
    snap.zone_states["Biotech_Area"] = "Crowded"
    guide = _guide(snap)
    guide.execute_task("SuggestRoute", {"destination": "B_GM1", "avoid_crowded": "true"})
    _, _, traj = guide.snapshot_state()
    assert guide.position == "B_GM1"
    assert traj[0].relaxed is True


def test_unreachable_destination_reports_failure():
    snap = snapshot_from_config()
    for key in [("P_South_Hub", "B_GM1"), ("B_GM1", "P_South_Hub"), ("B_GM1", "B_GM2"), ("B_GM2", "B_GM1")]:
        snap.edges[key]["blocked"] = True
    guide = _guide(snap)
    with pytest.raises(RouteUnavailable):
        guide.execute_task("LocateExhibit", {"target_name": "B_GM1"})
    with patch.object(guide, "publish") as pub:
        result = guide._handle("navigation.request", _Parcel({"task": "LocateExhibit", "task_id": "T1",
                                                             "params": {"target_name": "B_GM1"}}))
    assert result["ok"] is False
    assert "no passable route" in result["error"]
    assert pub.call_args[0][1]["ok"] is False


def test_cancel_stops_at_current_node():
    guide = _guide(snapshot_from_config())
    token = CancelToken()
    guide.step_listener = lambda step: token.cancel("replan") if step.to_node == "P_Info" else None
    message, cancelled = guide.execute_task("LocateExhibit", {"target_name": "B_GM1"}, cancel_token=token)
    assert cancelled is True
    assert guide.position == "P_Info"
    assert _path(guide) == ["P_Entrance", "P_Info"]


def test_concurrent_navigation_tasks_are_serialized():
    guide = GuideAgent(_EMPTY_BROKER, snapshot_loader=snapshot_from_config, seconds_per_meter=0.001)
    threads = [
        threading.Thread(target=guide.execute_task, args=("LocateExhibit", {"target_name": "B_AI1"}),
                         kwargs={"task_id": "a"}),
        threading.Thread(target=guide.execute_task, args=("LocateExhibit", {"target_name": "B_AI1"}),
                         kwargs={"task_id": "b"}),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    _, _, traj = guide.snapshot_state()
    # 只走一次：第二個任務等第一個走完時已在目的地
    assert [s.to_node for s in traj] == ["P_Info", "P_North_Hub", "B_AI1"]
    assert len({s.task_id for s in traj}) == 1


def test_non_navigation_task_uses_navigation_agent_behaviour():
    guide = _guide(snapshot_from_config())
    with patch("src.agents.navigation_agent.time.sleep", lambda *a, **k: None), \
         patch("src.agents.navigation_agent.get_crowd_hotspots", return_value=[]):
        message, cancelled = guide.execute_task("ExplainDirections", {"destination": "VR_Area"})
    assert cancelled is False
    assert guide.snapshot_state()[2] == []

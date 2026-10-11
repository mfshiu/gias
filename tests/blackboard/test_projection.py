"""觀察投影與監看目標的設定（G-03）：BlackboardAgent 不寫死展場邏輯。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.blackboard import projection as proj_mod
from src.blackboard.agent import BlackboardAgent
from src.blackboard.projection import ObservationProjection, projections_from_config, register_projection_preset
from src.blackboard.watcher import WatchTarget

_EMPTY_BROKER = {"broker": {"broker_name": "mqtt01", "mqtt01": {"broker_type": "empty"}}}


def _agent(blackboard_cfg=None, **kw):
    cfg = dict(_EMPTY_BROKER)
    if blackboard_cfg is not None:
        cfg["blackboard"] = blackboard_cfg
    agent = BlackboardAgent(cfg, **kw)
    agent._kg = MagicMock()
    agent.subscribe = MagicMock()
    agent.publish = MagicMock()
    return agent


def _observe(agent, obs_type, *entities):
    result = agent._handle_observe({"observation": {"observation_id": "o1", "observation_type": obs_type,
                                                    "entities": list(entities)}}, "observer-1")
    assert result["ok"] is True
    # 第一筆是 Observation，接著每個實體一筆 ObservedEntity；其餘是投影
    calls = agent._kg.write.call_args_list[1 + len(entities):]
    return [(c.args[0], c.args[1]) for c in calls]


def test_venue_projections_update_zone_and_booth():
    agent = _agent()
    [(cypher, params)] = _observe(agent, "pedestrian_flow", {
        "entity_type": "Zone", "entity_id": "zone_AI_Tech_Area", "properties": {"crowd_level": "Crowded"}})
    assert "MATCH (z:Zone {name: $zone_name})" in cypher
    assert params == {"zone_name": "AI_Tech_Area", "crowd_level": "Crowded"}

    agent._kg.reset_mock()
    [(cypher, params)] = _observe(agent, "facility_event", {
        "entity_type": "Booth", "entity_id": "B_AI1", "properties": {"status": "closed"}})
    assert "MATCH (b:Booth {id: $booth_id})" in cypher and params == {"booth_id": "B_AI1", "status": "closed"}


def test_non_matching_or_incomplete_entities_are_not_projected():
    agent = _agent()
    assert _observe(agent, "pedestrian_flow", {"entity_type": "Zone", "label": "AI_Tech_Area"}) == []   # 沒有 crowd_level
    agent._kg.reset_mock()
    assert _observe(agent, "facility_event", {"entity_type": "Zone", "label": "A", "properties": {"status": "x"}}) == []
    agent._kg.reset_mock()
    # 資料齊全，但觀察類型不是人流：不套用 Zone 規則
    assert _observe(agent, "visual_inspection", {"entity_type": "Zone", "label": "A",
                                                  "properties": {"crowd_level": "Crowded"}}) == []
    agent._kg.reset_mock()
    assert _observe(agent, "room_sensor", {"entity_type": "Room", "entity_id": "R301"}) == []


def test_other_domains_bring_their_own_rules():
    room = ObservationProjection(
        "room_sensor", "Room",
        lambda e: ("MATCH (r:Room {id: $id}) SET r.temperature = $t",
                   {"id": e["entity_id"], "t": e["properties"]["temperature"]}),
    )
    agent = _agent(projections=[room])
    assert _observe(agent, "room_sensor", {"entity_type": "Room", "entity_id": "R301",
                                           "properties": {"temperature": 28}}) == [
        ("MATCH (r:Room {id: $id}) SET r.temperature = $t", {"id": "R301", "t": 28})]
    agent._kg.reset_mock()
    assert _observe(agent, "pedestrian_flow", {"entity_type": "Zone", "label": "A",
                                               "properties": {"crowd_level": "Crowded"}}) == []


def test_projection_presets_from_config(monkeypatch):
    monkeypatch.setattr(proj_mod, "PROJECTION_PRESETS", dict(proj_mod.PROJECTION_PRESETS))
    assert _agent().projections == proj_mod.VENUE_PROJECTIONS          # 未設定：沿用 venue
    assert _agent({"projections": []}).projections == ()
    room = ObservationProjection("room_sensor", "Room", lambda e: None)
    register_projection_preset("office", [room])
    assert _agent({"projections": ["office"]}).projections == (room,)
    assert projections_from_config("office") == (room,)
    with pytest.raises(ValueError, match="Unknown blackboard projection preset 'nope'"):
        _agent({"projections": ["nope"]})


def test_a_failing_projection_does_not_stop_the_others():
    calls = []
    boom = ObservationProjection("obs", "A", lambda e: ("BOOM", {}))
    ok = ObservationProjection("obs", "A", lambda e: ("OK", {}))
    agent = _agent(projections=[boom, ok])

    def write(cypher, params=None):
        calls.append(cypher)
        if cypher == "BOOM":
            raise RuntimeError("constraint violated")

    agent._kg.write.side_effect = write
    agent._apply_observer_updates("obs", [{"entity_type": "A"}])
    assert calls == ["BOOM", "OK"]


# ----------------------------------------------------------------------
# 監看目標
# ----------------------------------------------------------------------
def _labels(agent):
    agent.on_connected()
    try:
        return [t.label for t in agent.watcher._watch_targets]
    finally:
        agent.watcher.stop()


def test_watch_targets_default_config_or_parameter():
    assert _labels(_agent()) == ["Agent", "Zone", "Task", "POI", "Booth"]
    configured = _agent({"watch_targets": [
        {"label": "Room", "properties": ["temperature"], "relationships": ["IN_BUILDING"]},
    ]})
    assert _labels(configured) == ["Room"]
    target = configured.watcher._watch_targets[0]
    assert (target.id_property, target.watch_properties, target.watch_relationships) == (
        "id", ["temperature"], ["IN_BUILDING"])
    assert _labels(_agent(watch_targets=[WatchTarget(label="Machine")])) == ["Machine"]
    with pytest.raises(ValueError, match="needs a label"):
        _agent({"watch_targets": [{"properties": ["x"]}]})

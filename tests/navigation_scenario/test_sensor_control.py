"""
Navigation Scenario 感測器 MQTT 控制協定單元測試

這些測試直接呼叫 `_handle_control` 與 `apply_event_override`，並 patch 掉
AgentFlow 的 `__init__`，避免實際連線到 MQTT broker。
"""

from __future__ import annotations

import random
from unittest.mock import patch, MagicMock

import pytest

from navigation_scenario.config import (
    STATE_CLOSED,
    STATE_CROWDED,
    STATE_NORMAL,
    STATE_SPARSE,
)
from navigation_scenario.sensors.base import (
    SENSORS_BROADCAST_TOPIC,
    EventOverride,
    SensorUpdate,
    sensors_kind_topic,
)
from navigation_scenario.sensors.pedestrian_flow import (
    PedestrianFlowSensorAgent,
)
from navigation_scenario.sensors.facility_event import (
    FacilityEventSensorAgent,
)
from navigation_scenario.sensors.visual import (
    VisualSensorAgent,
)
from navigation_scenario.sensors.digital import (
    DigitalSensorAgent,
)


# ----------------------------------------------------------------------
# 共用 builder：用 __new__ + 手動設定屬性，繞過 AgentFlow Agent.__init__
# ----------------------------------------------------------------------
def _make(cls, **overrides):
    """建立感測器實例但跳過 Agent.__init__（不連 MQTT）。"""
    inst = cls.__new__(cls)

    # ScenarioSensorAgent.__init__ 會設定的最小屬性集
    inst._sensor_id = f"{cls.__name__}_test"
    inst._poll_interval = 2.0
    inst._poll_jitter = 0.5
    inst._contact_probability = 0.5
    inst._rng = random.Random(0)
    inst._running = False
    inst._paused = False
    inst._thread = None
    inst._updates_sent = 0
    inst._overrides = []
    import threading
    inst._overrides_lock = threading.Lock()

    # 模擬 publish：把每次發送收集進 inst._published
    inst._published = []
    inst.publish = lambda topic, payload: inst._published.append((topic, payload))
    inst.name = cls.__name__

    # 讓子類別自家的 __init__ 內容（如 self._zones / self._block_probability ...）
    # 透過 overrides 注入
    for k, v in overrides.items():
        setattr(inst, k, v)
    return inst


def _make_pedestrian(**overrides):
    from navigation_scenario.config import ZONES
    base = dict(
        _zones=[z for z in ZONES if z != "Exit_Hall"],
        _last_crowd={},
        _last_density={},
        _crowd_weights=[0.25, 0.55, 0.20],
        _resample_probability=0.55,
    )
    base.update(overrides)
    return _make(PedestrianFlowSensorAgent, **base)


def _make_visual(**overrides):
    from navigation_scenario.config import EDGES
    edge_pool = [(a, b) for a, b, _ in EDGES]
    base = dict(
        _block_probability=0.65,
        _clear_probability=0.35,
        _blocked_edges=set(),
        _edge_pool=edge_pool,
    )
    base.update(overrides)
    return _make(VisualSensorAgent, **base)


def _make_digital(**overrides):
    from navigation_scenario.config import ZONES
    base = dict(
        _zones=[z for z in ZONES if z not in ("Main_Hall", "Exit_Hall")],
        _zone_state={},
        _api_event_weights=[0.35, 0.30, 0.25, 0.10],
        _skip_unchanged_probability=0.6,
    )
    base.update(overrides)
    return _make(DigitalSensorAgent, **base)


def _make_facility(**overrides):
    from navigation_scenario.config import BOOTHS, POIS
    base = dict(
        _booths=list(BOOTHS),
        _facilities=[
            {"id": p["id"], "name": p["name"], "type": "poi"}
            for p in POIS
            if p["id"] in ("P_Info", "P_Restroom_N", "P_Restroom_S", "P_Cafe", "P_Exit")
        ],
        _last_booth_status={},
        _booth_status_weights=[0.45, 0.35, 0.20],
        _booth_resample_probability=0.22,
        _facility_outage_probability=0.08,
    )
    base.update(overrides)
    return _make(FacilityEventSensorAgent, **base)


# ----------------------------------------------------------------------
# Topic 命名與層級
# ----------------------------------------------------------------------
class TestControlTopics:
    def test_broadcast_topic(self):
        assert SENSORS_BROADCAST_TOPIC == "navigation_scenario.sensors.control"

    def test_kind_topic_helper(self):
        assert sensors_kind_topic("visual") == "navigation_scenario.sensors.visual.control"

    def test_instance_topic(self):
        s = _make_pedestrian()
        assert s.control_topic.startswith("navigation_scenario.sensor.")
        assert s.control_topic.endswith(".control")
        assert s.kind_control_topic == "navigation_scenario.sensors.pedestrian_flow.control"


# ----------------------------------------------------------------------
# 共通指令：status / pause / resume / set_contact_probability / set_interval
# ----------------------------------------------------------------------
class TestCommonControl:
    def test_status_report(self):
        s = _make_pedestrian()
        result = s._handle_control(s.control_topic, {"command": "status"})

        assert result["ok"] is True
        assert result["sensor_kind"] == "pedestrian_flow"
        assert result["handled_event_kinds"] == [
            "crowd_congestion",
            "crowd_clear",
            "crowd_sparse",
        ]
        assert "random_params" in result
        assert "crowd_weights" in result["random_params"]

    def test_set_contact_probability(self):
        s = _make_pedestrian()
        result = s._handle_control(
            s.control_topic,
            {"command": "set_contact_probability", "probability": 0.9},
        )
        assert result["ok"] is True
        assert s.contact_probability == pytest.approx(0.9)

    def test_set_interval_clamped(self):
        s = _make_pedestrian()
        result = s._handle_control(
            s.control_topic, {"command": "set_interval", "interval": 0.05}
        )
        assert result["ok"] is True
        # 內部 clamp 至 0.2
        assert s.poll_interval_sec == pytest.approx(0.2)

    def test_pause_resume(self):
        s = _make_pedestrian()
        assert s.is_paused is False
        s._handle_control(s.control_topic, {"command": "pause"})
        assert s.is_paused is True
        s._handle_control(s.control_topic, {"command": "resume"})
        assert s.is_paused is False

    def test_unknown_command(self):
        s = _make_pedestrian()
        result = s._handle_control(s.control_topic, {"command": "not_real"})
        assert result["ok"] is False
        assert "Unknown command" in result["error"]


# ----------------------------------------------------------------------
# set_random_param：每個感測器都能接收訊息調整隨機數值
# ----------------------------------------------------------------------
class TestSetRandomParam:
    def test_pedestrian_crowd_weights(self):
        s = _make_pedestrian()
        result = s._handle_control(
            s.control_topic,
            {
                "command": "set_random_param",
                "name": "crowd_weights",
                "value": [0.8, 0.1, 0.1],
            },
        )
        assert result["ok"] is True
        assert s._crowd_weights == [0.8, 0.1, 0.1]

    def test_pedestrian_invalid_weights_rejected(self):
        s = _make_pedestrian()
        result = s._handle_control(
            s.control_topic,
            {"command": "set_random_param", "name": "crowd_weights", "value": [0, 0, 0]},
        )
        assert result["ok"] is False

    def test_visual_block_probability(self):
        s = _make_visual()
        result = s._handle_control(
            s.control_topic,
            {"command": "set_random_param", "name": "block_probability", "value": 0.95},
        )
        assert result["ok"] is True
        assert s._block_probability == pytest.approx(0.95)

    def test_visual_clear_probability_clamped(self):
        s = _make_visual()
        result = s._handle_control(
            s.control_topic,
            {"command": "set_random_param", "name": "clear_probability", "value": 5.0},
        )
        assert result["ok"] is True
        assert s._clear_probability == pytest.approx(1.0)

    def test_digital_api_event_weights(self):
        s = _make_digital()
        result = s._handle_control(
            s.control_topic,
            {
                "command": "set_random_param",
                "name": "api_event_weights",
                "value": [0.7, 0.1, 0.1, 0.1],
            },
        )
        assert result["ok"] is True
        assert s._api_event_weights == [0.7, 0.1, 0.1, 0.1]

    def test_facility_booth_status_weights(self):
        s = _make_facility()
        result = s._handle_control(
            s.control_topic,
            {
                "command": "set_random_param",
                "name": "booth_status_weights",
                "value": [0.1, 0.1, 0.8],
            },
        )
        assert result["ok"] is True
        assert s._booth_status_weights == [0.1, 0.1, 0.8]

    def test_unknown_random_param_rejected(self):
        s = _make_pedestrian()
        result = s._handle_control(
            s.control_topic,
            {"command": "set_random_param", "name": "nonexistent", "value": 1.0},
        )
        assert result["ok"] is False


# ----------------------------------------------------------------------
# inject_event：核心動態事件注入
# ----------------------------------------------------------------------
class TestInjectEvent:
    def test_crowd_congestion_forces_zone_crowded(self):
        s = _make_pedestrian(_zones=["AI_Tech_Area", "Robotics_Area", "Gaming_Area"])
        result = s._handle_control(
            s.control_topic,
            {
                "command": "inject_event",
                "kind": "crowd_congestion",
                "target": "AI_Tech_Area",
                "ttl_samples": 1,
            },
        )
        assert result["ok"] is True
        assert result["accepted"] is True
        # 立即被消費並寫進 Blackboard
        assert result["sent"] is True
        # 檢查發送內容中含 AI_Tech_Area 強制成 Crowded
        assert len(s._published) >= 1
        topic, msg = s._published[-1]
        assert msg["command"] == "write"
        rows = msg["params"]["rows"]
        ai_row = next(r for r in rows if r["zone"] == "AI_Tech_Area")
        assert ai_row["level"] == STATE_CROWDED

    def test_crowd_clear_forces_normal(self):
        s = _make_pedestrian(_zones=["AI_Tech_Area"])
        s._last_crowd["AI_Tech_Area"] = STATE_CROWDED

        result = s._handle_control(
            s.control_topic,
            {
                "command": "inject_event",
                "kind": "crowd_clear",
                "target": "AI_Tech_Area",
            },
        )
        assert result["accepted"] is True
        _, msg = s._published[-1]
        ai = next(r for r in msg["params"]["rows"] if r["zone"] == "AI_Tech_Area")
        assert ai["level"] == STATE_NORMAL

    def test_inject_event_unsupported_kind_skipped(self):
        s = _make_pedestrian()
        # area_closure 是 Digital 處理的，PedestrianFlow 應該跳過
        result = s._handle_control(
            s.control_topic,
            {
                "command": "inject_event",
                "kind": "area_closure",
                "target": "AI_Tech_Area",
            },
        )
        assert result["ok"] is True
        assert result["accepted"] is False
        assert result["skipped"] is True

    def test_visual_route_detour(self):
        s = _make_visual()
        result = s._handle_control(
            s.control_topic,
            {
                "command": "inject_event",
                "kind": "route_detour",
                "target": {"from": "B_RB1", "to": "B_RB2"},
                "payload": {"obstacle_type": "construction"},
            },
        )
        assert result["accepted"] is True
        topic, msg = s._published[-1]
        assert msg["params"]["a_id"] == "B_RB1"
        assert msg["params"]["b_id"] == "B_RB2"
        assert msg["params"]["blocked"] is True
        assert msg["params"]["obstacle"] == "construction"

    def test_visual_route_clear(self):
        s = _make_visual()
        s._blocked_edges.add(("B_RB1", "B_RB2"))
        result = s._handle_control(
            s.control_topic,
            {
                "command": "inject_event",
                "kind": "route_clear",
                "target": "B_RB1->B_RB2",
            },
        )
        assert result["accepted"] is True
        _, msg = s._published[-1]
        assert msg["params"]["blocked"] is False
        assert msg["params"]["obstacle"] == "clear"
        assert ("B_RB1", "B_RB2") not in s._blocked_edges

    def test_digital_area_closure(self):
        s = _make_digital()
        result = s._handle_control(
            s.control_topic,
            {
                "command": "inject_event",
                "kind": "area_closure",
                "target": "AI_Tech_Area",
            },
        )
        assert result["accepted"] is True
        _, msg = s._published[-1]
        assert msg["params"]["zone"] == "AI_Tech_Area"
        assert msg["params"]["state"] == STATE_CLOSED
        assert msg["params"]["event_type"] == "zone_closure"

    def test_digital_area_reopen(self):
        s = _make_digital()
        s._zone_state["AI_Tech_Area"] = STATE_CLOSED
        result = s._handle_control(
            s.control_topic,
            {
                "command": "inject_event",
                "kind": "area_reopen",
                "target": "AI_Tech_Area",
            },
        )
        assert result["accepted"] is True
        _, msg = s._published[-1]
        assert msg["params"]["state"] == STATE_NORMAL

    def test_facility_booth_closure(self):
        s = _make_facility()
        result = s._handle_control(
            s.control_topic,
            {
                "command": "inject_event",
                "kind": "booth_closure",
                "target": "B_AI1",
            },
        )
        assert result["accepted"] is True
        # FacilityEvent 是多語句寫入
        sent_messages = [m for _, m in s._published]
        # 第一個是 _UPDATE_BOOTHS 的 rows
        booth_msg = next(
            m for m in sent_messages if "rows" in m.get("params", {}) and any(
                r.get("id", "").startswith("B_") for r in m["params"]["rows"]
            )
        )
        forced_row = next(
            r for r in booth_msg["params"]["rows"] if r["id"] == "B_AI1"
        )
        assert forced_row["status"] == "closed"


# ----------------------------------------------------------------------
# TTL：覆寫持續多輪
# ----------------------------------------------------------------------
class TestOverrideTTL:
    def test_ttl_consumed_over_samples(self):
        s = _make_pedestrian(_zones=["AI_Tech_Area"])
        s.apply_event_override("crowd_congestion", "AI_Tech_Area", {}, ttl_samples=3)
        assert s.pending_override_count == 1

        s.sample_update()
        # 3 → 2
        assert s.pending_override_count == 1

        s.sample_update()
        # 2 → 1
        assert s.pending_override_count == 1

        s.sample_update()
        # 1 → 0
        assert s.pending_override_count == 0

    def test_clear_overrides(self):
        s = _make_pedestrian()
        s.apply_event_override("crowd_congestion", "AI_Tech_Area", {}, ttl_samples=10)
        s.apply_event_override("crowd_sparse", "Gaming_Area", {}, ttl_samples=10)
        assert s.pending_override_count == 2

        result = s._handle_control(s.control_topic, {"command": "clear_overrides"})
        assert result["ok"] is True
        assert result["cleared"] == 2
        assert s.pending_override_count == 0


# ----------------------------------------------------------------------
# 多 target 與多事件併發
# ----------------------------------------------------------------------
class TestMultiTarget:
    def test_pedestrian_target_list(self):
        s = _make_pedestrian(_zones=["AI_Tech_Area", "Robotics_Area"])
        result = s._handle_control(
            s.control_topic,
            {
                "command": "inject_event",
                "kind": "crowd_congestion",
                "target": ["AI_Tech_Area", "Robotics_Area"],
            },
        )
        assert result["accepted"] is True
        _, msg = s._published[-1]
        rows = {r["zone"]: r for r in msg["params"]["rows"]}
        assert rows["AI_Tech_Area"]["level"] == STATE_CROWDED
        assert rows["Robotics_Area"]["level"] == STATE_CROWDED

    def test_visual_multiple_injections(self):
        s = _make_visual()
        s.apply_event_override("route_detour", {"from": "B_RB1", "to": "B_RB2"})
        s.apply_event_override("route_detour", {"from": "B_GM1", "to": "B_GM2"})

        update = s.sample_update()
        assert update is not None
        assert update.statements is not None
        assert len(update.statements) == 2


# ----------------------------------------------------------------------
# Payload 容錯：AgentFlow Parcel 包裝形式
# ----------------------------------------------------------------------
class TestPayloadShape:
    def test_parcel_like_payload(self):
        """模擬 AgentFlow 從 broker 收到時 payload 帶 .content 的情況。"""
        s = _make_pedestrian()
        parcel = MagicMock()
        parcel.content = {"command": "status"}

        result = s._handle_control(s.control_topic, parcel)
        assert result["ok"] is True

    def test_empty_payload(self):
        s = _make_pedestrian()
        result = s._handle_control(s.control_topic, None)
        assert result["ok"] is False

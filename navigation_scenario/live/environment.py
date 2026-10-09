"""
live 模式與 Blackboard KG 的互動：事件注入、機器人位置。

事件注入有兩種方式：
- DirectEventApplier（預設）：直接把事件效果寫進 Blackboard KG（與感測器使用相同的 Cypher 語意），
  可重現、不需要 run_sensors；寫入後由 BlackboardAgent 的 watcher 偵測並通知訂閱者
- SensorEventApplier：沿用模擬模式的 MQTT 注入，由 run_sensors 的感測器套用（會受感測器隨機性影響）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from navigation_scenario.config import (
    EVENT_KIND_CLOSURE,
    EVENT_KIND_CROWD,
    EVENT_KIND_DETOUR,
    GUIDE_ROBOT_ID,
    STATE_CLOSED,
    STATE_CROWDED,
)
from navigation_scenario.test_cases import TestCase

_SET_ZONE_STATE = """
MATCH (z:Zone {name: $zone})
OPTIONAL MATCH (z)-[old:CURRENT_STATE]->()
DELETE old
WITH DISTINCT z
MATCH (st:State {status_name: $state})
CREATE (z)-[:CURRENT_STATE {updated_at: datetime(), source: 'live_benchmark'}]->(st)
"""

_SET_EDGE_BLOCKED = """
MATCH (a) WHERE a.id = $a_id
MATCH (b) WHERE b.id = $b_id
MATCH (a)-[r:CONNECTED_TO]->(b)
SET r.blocked = $blocked, r.obstacle_type = $obstacle, r.update_source = 'live_benchmark'
WITH a, b
MATCH (b)-[r2:CONNECTED_TO]->(a)
SET r2.blocked = $blocked, r2.obstacle_type = $obstacle, r2.update_source = 'live_benchmark'
"""

_SET_ROBOT_POSITION = """
MERGE (a:Agent {agent_id: $agent_id})
SET a.name = 'Guide Robot'
WITH a
OPTIONAL MATCH (a)-[old:CURRENT_POSITION]->()
DELETE old
WITH DISTINCT a
MATCH (n) WHERE n.id = $node OR (n:Zone AND n.name = $node)
WITH a, n LIMIT 1
CREATE (a)-[:CURRENT_POSITION {updated_at: datetime()}]->(n)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _writer(adapter: Any) -> Callable[[str, dict[str, Any]], Any]:
    return adapter.write_explicit if hasattr(adapter, "write_explicit") else adapter.write


class DirectEventApplier:
    """把案例的動態事件直接寫進 Blackboard KG。"""

    def __init__(self, adapter: Any):
        self._write = _writer(adapter)

    def apply(self, case: TestCase) -> dict[str, Any]:
        ev = case.event
        if ev.kind == EVENT_KIND_CROWD:
            self._write(_SET_ZONE_STATE, {"zone": ev.target, "state": STATE_CROWDED})
        elif ev.kind == EVENT_KIND_CLOSURE:
            self._write(_SET_ZONE_STATE, {"zone": ev.target, "state": STATE_CLOSED})
        elif ev.kind == EVENT_KIND_DETOUR and isinstance(ev.target, dict):
            self._write(_SET_EDGE_BLOCKED, {
                "a_id": ev.target.get("from"), "b_id": ev.target.get("to"), "blocked": True,
                "obstacle": (ev.payload or {}).get("obstacle_type", "barrier"),
            })
        else:
            raise ValueError(f"unsupported event for direct injection: {ev.kind} {ev.target!r}")
        return {"method": "direct", "kind": ev.kind, "target": ev.target, "applied_at": _now()}


class SensorEventApplier:
    """沿用模擬模式：經 MQTT 送 inject_event 給 run_sensors 的感測器。"""

    def __init__(self, injector: Any):
        self.injector = injector

    def apply(self, case: TestCase) -> dict[str, Any]:
        from navigation_scenario.runner import _inject_via_mqtt

        command = _inject_via_mqtt(self.injector, case)
        return {"method": "sensors", "kind": case.event.kind, "target": case.event.target,
                "applied_at": _now(), "command": command}


class KGPositionWriter:
    """把機器人位置寫成 (:Agent {agent_id})-[:CURRENT_POSITION]->(節點)，供重規劃時查詢。"""

    def __init__(self, adapter: Any, *, agent_id: str = GUIDE_ROBOT_ID):
        self._write = _writer(adapter)
        self.agent_id = agent_id

    def __call__(self, node: str) -> None:
        self._write(_SET_ROBOT_POSITION, {"agent_id": self.agent_id, "node": node})

"""blackboard.control 的存取控制（S-01）：共用金鑰、唯讀查詢、封鎖危險語法。"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.blackboard import client as bb_client
from src.blackboard.access import AccessPolicy, check_cypher, control_token, with_token
from src.blackboard.agent import BlackboardAgent

TOKEN = "s3cret-token"
_EMPTY_BROKER = {"broker": {"broker_name": "mqtt01", "mqtt01": {"broker_type": "empty"}}}


@pytest.mark.parametrize("cypher, reason", [
    ("LOAD CSV FROM 'file:///etc/passwd' AS row RETURN row", "LOAD CSV"),
    ("CALL dbms.components()", "dbms / apoc procedures"),
    ("CALL apoc.load.json('http://evil')", "dbms / apoc procedures"),
    ("CALL db.index.fulltext.createNodeIndex('x', ['A'], ['b'])", "schema procedures"),
    ("DROP INDEX action_desc_vec IF EXISTS", "index / constraint changes"),
    ("CREATE VECTOR INDEX x FOR (a:A) ON (a.e)", "index / constraint changes"),
    ("CREATE CONSTRAINT c FOR (n:A) REQUIRE n.id IS UNIQUE", "index / constraint changes"),
    ("CREATE OR REPLACE DATABASE blackboard", "database / user administration"),
    ("ALTER USER neo4j SET PASSWORD 'x'", "database / user administration"),
    ("GRANT ROLE admin TO bob", "privilege administration"),
    ("USE system SHOW USERS", "switching databases"),
    ("MATCH (n) CALL { USE system SHOW USERS } RETURN n", "switching databases"),
])
def test_dangerous_cypher_is_rejected(cypher, reason):
    assert check_cypher(cypher) == reason


def _all_client_cypher() -> list[str]:
    """repo 中經由黑板代理執行的 Cypher：一律不可被誤擋。"""
    from navigation_scenario.config import NAVIGATION_PROFILE
    from navigation_scenario.live import environment
    from navigation_scenario.sensors import digital, facility_event, pedestrian_flow, visual
    from src.blackboard.client import VENUE_STATE_PROBES
    from src.blackboard.projection import VENUE_PROJECTIONS
    from src.core.intention import store
    from src.run_intentional_agent import EXPO_PROFILE

    statements = [
        *VENUE_STATE_PROBES.values(), *NAVIGATION_PROFILE.env_fact_queries.values(),
        *EXPO_PROFILE.env_fact_queries.values(), store._UPSERT, store._MARK_INTERRUPTED,
        digital._UPDATE_ZONE_STATE, facility_event._UPDATE_BOOTHS, facility_event._UPDATE_FACILITIES,
        pedestrian_flow._UPDATE_CYPHER, visual._UPDATE_EDGE,
        environment._SET_ZONE_STATE, environment._SET_EDGE_BLOCKED, environment._SET_ROBOT_POSITION,
    ]
    entity = {"entity_type": "x", "entity_id": "zone_A", "label": "A", "properties": {"crowd_level": "Crowded",
                                                                                      "status": "open"}}
    statements += [p.build(entity)[0] for p in VENUE_PROJECTIONS]
    return statements


def test_cypher_used_by_gias_clients_is_allowed():
    statements = _all_client_cypher()
    assert len(statements) >= 15
    assert [s for s in statements if check_cypher(s)] == []


def test_policy_without_token_accepts_everything():
    assert AccessPolicy().authorized(None) and AccessPolicy.from_config({}).authorized("anything")
    policy = AccessPolicy(TOKEN)
    assert policy.authorized(TOKEN)
    assert not policy.authorized(None) and not policy.authorized("wrong") and not policy.authorized(123)


# ----------------------------------------------------------------------
# BlackboardAgent
# ----------------------------------------------------------------------
def _agent(token=None):
    cfg = dict(_EMPTY_BROKER)
    if token:
        cfg["blackboard"] = {"access": {"token": token}}
    agent = BlackboardAgent(cfg)
    agent._kg = MagicMock()
    agent._kg.read.return_value = [{"n": 1}]
    agent.subscribe = MagicMock()
    agent.publish = MagicMock()
    return agent


def _control(agent, **payload):
    return agent._handle_control("blackboard.control", payload)


@pytest.mark.parametrize("command", [
    {"command": "query", "cypher": "MATCH (n) RETURN n"},
    {"command": "write", "cypher": "MATCH (n) DETACH DELETE n"},
    {"command": "subscribe", "pattern": "Zone/*/*"},
    {"command": "observe", "observation": {"observation_id": "o1", "entities": []}},
])
def test_commands_need_the_token_when_configured(command):
    agent = _agent(TOKEN)
    assert _control(agent, **command) == {"ok": False, "error": "unauthorized"}
    assert _control(agent, **command, token="wrong") == {"ok": False, "error": "unauthorized"}
    assert _control(agent, **command, token=TOKEN)["ok"] is True


def test_without_token_commands_still_work_and_a_warning_is_logged(caplog):
    agent = _agent()
    assert _control(agent, command="query", cypher="MATCH (n) RETURN n")["ok"] is True
    with caplog.at_level(logging.WARNING):
        agent.on_connected()
    agent.watcher.stop()
    assert "no [blackboard.access] token configured" in caplog.text


def test_query_runs_read_only_and_forbidden_cypher_is_rejected():
    agent = _agent(TOKEN)
    assert _control(agent, command="query", cypher="MATCH (n) RETURN n", token=TOKEN) == {"ok": True, "rows": [{"n": 1}]}
    agent._kg.read.assert_called_once()
    agent._kg.query.assert_not_called()
    agent._kg.write.assert_not_called()
    for command in ("query", "write"):
        reply = _control(agent, command=command, cypher="LOAD CSV FROM 'file:///x' AS r RETURN r", token=TOKEN)
        assert reply == {"ok": False, "error": "forbidden: LOAD CSV"}
    assert agent._kg.read.call_count == 1 and agent._kg.write.call_count == 0


# ----------------------------------------------------------------------
# 用戶端附上金鑰
# ----------------------------------------------------------------------
def test_clients_attach_their_configured_token():
    with_cfg = SimpleNamespace(config={"blackboard": {"access": {"token": TOKEN}}})
    ia_style = SimpleNamespace(agent_config={"blackboard": {"access": {"token": TOKEN}}})
    plain = SimpleNamespace(config={"broker": {}})
    assert control_token(with_cfg) == control_token(ia_style) == TOKEN
    assert with_token(plain, {"command": "query"}) == {"command": "query"}
    assert control_token(MagicMock()) is None                          # 不是 dict 的設定不採用

    sent = []
    agent = SimpleNamespace(agent_id="ia", config={"blackboard": {"access": {"token": TOKEN}}},
                            publish_sync=lambda topic, payload, timeout: sent.append(payload) or
                            SimpleNamespace(content={"ok": True, "rows": []}))
    assert bb_client.try_query_blackboard(agent, "MATCH (n) RETURN n") == []
    assert bb_client.write_blackboard(agent, "MATCH (n) SET n.x = 1")
    assert [p["token"] for p in sent] == [TOKEN, TOKEN]


def test_sensors_and_observers_attach_the_token():
    from navigation_scenario.sensors.base import SensorUpdate
    from navigation_scenario.sensors.pedestrian_flow import PedestrianFlowSensorAgent
    from src.observer.base import ObserverAgent

    cfg = dict(_EMPTY_BROKER, blackboard={"access": {"token": TOKEN}})
    sensor = PedestrianFlowSensorAgent(agent_config=cfg)
    sensor.publish = MagicMock()
    sensor._send_to_blackboard(SensorUpdate(kind="write", cypher="MATCH (z:Zone) SET z.x = 1", params={}))
    sensor._send_to_blackboard(SensorUpdate(kind="observe", observation={"observation_id": "o"}))
    assert [c.args[1]["token"] for c in sensor.publish.call_args_list] == [TOKEN, TOKEN]

    observer = MagicMock(spec=ObserverAgent)
    observer.config, observer.observer_id = cfg, "obs-1"
    observation = MagicMock(saliency=1, entities=[])
    observation.to_dict.return_value = {"observation_id": "o"}
    ObserverAgent.submit_to_blackboard(observer, observation)
    assert observer.publish.call_args.args[1]["token"] == TOKEN

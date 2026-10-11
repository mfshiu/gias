"""MQTT 連線安全檢查（S-02）。"""

from __future__ import annotations

import logging

import pytest

from src.blackboard.agent import BlackboardAgent
from src.security import broker_security_issues, tls_enabled


def _cfg(**opts):
    base = {"broker_type": "mqtt", "host": "localhost", "port": 1883}
    return {"broker": {"broker_name": "mqtt01", "mqtt01": {**base, **opts}}}


def test_local_plaintext_connection_is_noted_at_info():
    [(level, message)] = broker_security_issues(_cfg(username="gias", password="pw"))
    assert level == logging.INFO and "localhost:1883 is not encrypted" in message


def test_remote_plaintext_anonymous_connection_warns_twice():
    issues = broker_security_issues(_cfg(host="mqtt.example.org", port=1884))
    assert [lvl for lvl, _ in issues] == [logging.WARNING, logging.WARNING]
    assert "not encrypted" in issues[0][1] and "no username" in issues[1][1]


@pytest.mark.parametrize("tls", [True, "true", {"ca_certs": "ca.crt"}, {"enabled": True}])
def test_tls_with_credentials_has_no_issues(tls):
    assert broker_security_issues(_cfg(host="mqtt.example.org", port=8883, username="gias", tls=tls)) == []


@pytest.mark.parametrize("tls, enabled", [
    (None, False), (False, False), ("off", False), ({"enabled": False}, False), ({"enabled": "no"}, False),
    (True, True), ({}, True),
])
def test_tls_enabled_matches_agentflow(tls, enabled):
    assert tls_enabled(tls) is enabled


def test_insecure_tls_warns():
    [(level, message)] = broker_security_issues(
        _cfg(host="mqtt.example.org", username="gias", tls={"ca_certs": "ca.crt", "insecure": True}))
    assert level == logging.WARNING and "does not verify the broker host name" in message


def test_other_broker_types_and_flat_config():
    assert broker_security_issues({"broker": {"broker_name": "b", "b": {"broker_type": "empty"}}}) == []
    flat = {"broker": {"broker_name": "mqtt01"}, "broker.mqtt01": {"broker_type": "mqtt", "host": "10.0.0.5"}}
    assert len(broker_security_issues(flat)) == 2
    assert broker_security_issues({}) != []                  # 未設定 broker：預設 localhost:1883 明文、匿名


def test_services_log_the_check_at_startup(caplog):
    cfg = _cfg(host="mqtt.example.org")
    with caplog.at_level(logging.WARNING):
        BlackboardAgent(cfg)
    assert "mqtt.example.org:1883 is not encrypted" in caplog.text

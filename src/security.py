# src/security.py
"""
連線安全檢查（S-02）：啟動時檢查 MQTT broker 設定，明文或匿名連線時記錄提醒。

設定方式見 docs/mqtt_security.md：broker 啟用 TLS listener、關閉匿名連線、使用密碼檔與 ACL；
gias.toml 的 [broker.<name>] 設定 username / password 與 [broker.<name>.tls]（需要支援 tls 選項的 AgentFlow）。
連到本機（localhost）的明文連線記為 INFO，連到其他主機記為 WARNING。
"""

from __future__ import annotations

import logging
from typing import Any

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_TRUE = frozenset({"true", "1", "yes", "on"})


def _truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in _TRUE)


def broker_options(config: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """目前使用的 broker 名稱與設定（[broker] broker_name 指到的 [broker.<name>]）。"""
    broker = config.get("broker") if isinstance(config.get("broker"), dict) else {}
    name = broker.get("broker_name")
    opts = broker.get(name) if name else None
    if not isinstance(opts, dict) and name:
        opts = config.get(f"broker.{name}")
    return name, opts if isinstance(opts, dict) else {}


def tls_enabled(tls: Any) -> bool:
    """與 AgentFlow mqtt_broker 的 tls 選項相同的判斷：true、或 enabled 不為 false 的 table。"""
    if isinstance(tls, dict):
        enabled = tls.get("enabled", True)
        return enabled is True or (isinstance(enabled, str) and enabled.strip().lower() not in ("false", "0", "no", "off"))
    return _truthy(tls)


def broker_security_issues(config: dict[str, Any]) -> list[tuple[int, str]]:
    """回傳 [(log level, 說明)]；設定安全時為空串列。"""
    name, opts = broker_options(config)
    if str(opts.get("broker_type", "mqtt")).lower() != "mqtt":
        return []
    host = str(opts.get("host", "localhost"))
    port = opts.get("port", 1883)
    level = logging.INFO if host.strip().lower() in _LOOPBACK else logging.WARNING
    issues: list[tuple[int, str]] = []
    tls = opts.get("tls")
    if not tls_enabled(tls):
        issues.append((level, f"MQTT connection to {host}:{port} is not encrypted; credentials and messages "
                              f"travel in plaintext. Configure [broker.{name}.tls] (see docs/mqtt_security.md)."))
    elif isinstance(tls, dict) and _truthy(tls.get("insecure")):
        issues.append((logging.WARNING, f"MQTT TLS to {host}:{port} does not verify the broker host name "
                                        f"(tls.insecure); use it for testing only."))
    if not opts.get("username"):
        issues.append((level, f"MQTT connection to {host}:{port} has no username; the broker must accept "
                              f"anonymous clients. Disable anonymous access on the broker (see docs/mqtt_security.md)."))
    return issues


def log_broker_security(config: dict[str, Any], logger: logging.Logger) -> None:
    for level, message in broker_security_issues(config or {}):
        logger.log(level, message)

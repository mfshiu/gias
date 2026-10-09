"""
Navigation Scenario：動態事件 MQTT 注入器

直接以 paho-mqtt 連線 broker，向感測器發送 `inject_event` 訊息。

這個注入器不繼承 AgentFlow Agent，只負責「把訊息送上 broker」，
感測器收到後會更新 KG（透過已建好的 MQTT 控制介面）。

訊息格式遵循 AgentFlow `TextParcel`：`text/json|{json}`。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

import paho.mqtt.client as mqtt

from src.app_helper import get_agent_config

from navigation_scenario.config import (
    EVENT_KIND_CLOSURE,
    EVENT_KIND_CROWD,
    EVENT_KIND_DETOUR,
)


# 與 AgentFlow TextParcel.HEAD 一致
_PARCEL_HEAD = b"text/json|"
_PARCEL_VERSION = 3

# 事件 kind 對應的「應該由哪個感測器處理」
_EVENT_KIND_TO_SENSOR_KIND: dict[str, str] = {
    EVENT_KIND_CROWD: "pedestrian_flow",
    EVENT_KIND_CLOSURE: "digital",
    EVENT_KIND_DETOUR: "visual",
    # 反向：clear/復原事件對應同類感測器
    "crowd_clear": "pedestrian_flow",
    "crowd_sparse": "pedestrian_flow",
    "area_reopen": "digital",
    "route_clear": "visual",
    "booth_closure": "facility_event",
    "booth_reopen": "facility_event",
    "facility_outage": "facility_event",
    "facility_restore": "facility_event",
}


def _wrap_parcel(content: dict[str, Any]) -> bytes:
    """把 dict 包成 TextParcel 序列化格式。"""
    body = {
        "version": _PARCEL_VERSION,
        "content": content,
        "topic_return": None,
        "error": None,
    }
    return _PARCEL_HEAD + json.dumps(body, ensure_ascii=False).encode("utf-8")


# =============================================================================
# 主類別
# =============================================================================
class MqttEventInjector:
    """
    透過 MQTT 發送 `inject_event` 訊息給 navigation_scenario 感測器。

    使用方式：
        with MqttEventInjector() as injector:
            injector.inject_crowd_congestion("AI_Tech_Area", ttl_samples=3)
            injector.inject_area_closure("VR_Area")
            injector.inject_route_detour("B_RB1", "B_RB2", obstacle_type="construction")
    """

    BROADCAST_TOPIC = "navigation_scenario.sensors.control"
    KIND_TOPIC_FMT = "navigation_scenario.sensors.{kind}.control"

    def __init__(
        self,
        *,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_id: Optional[str] = None,
        connect_timeout_sec: float = 5.0,
    ):
        cfg = get_agent_config()
        broker_name = cfg.get("broker", {}).get("broker_name", "mqtt01")
        bcfg = cfg.get("broker", {}).get(broker_name, {})

        self._host = host or bcfg.get("host", "localhost")
        self._port = int(port or bcfg.get("port", 1883))
        self._username = username if username is not None else bcfg.get("username")
        self._password = password if password is not None else bcfg.get("password")
        self._client_id = client_id or f"nav_event_injector_{uuid.uuid4().hex[:8]}"
        self._connect_timeout = float(connect_timeout_sec)

        self._client: Optional[mqtt.Client] = None
        self._connected = False

    # ------------------------------------------------------------------
    # 連線生命週期
    # ------------------------------------------------------------------
    def __enter__(self) -> "MqttEventInjector":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

    def connect(self) -> None:
        if self._connected:
            return
        client = mqtt.Client(client_id=self._client_id)
        if self._username:
            client.username_pw_set(self._username, self._password or "")

        def _on_connect(_cli, _ud, _flags, rc, *args):
            self._connected = (rc == 0)

        client.on_connect = _on_connect
        client.connect(self._host, self._port, keepalive=30)
        client.loop_start()

        # 等連線完成
        deadline = time.time() + self._connect_timeout
        while not self._connected and time.time() < deadline:
            time.sleep(0.05)

        if not self._connected:
            client.loop_stop()
            raise RuntimeError(
                f"MqttEventInjector: 無法連線到 broker {self._host}:{self._port}"
            )
        self._client = client

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self._connected = False

    # ------------------------------------------------------------------
    # 低階：raw publish
    # ------------------------------------------------------------------
    def publish_command(
        self,
        topic: str,
        command: dict[str, Any],
        *,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        """直接向某 topic 發送一個 command dict（自動包成 Parcel）。"""
        if not self._client or not self._connected:
            raise RuntimeError("MqttEventInjector 尚未連線；請先呼叫 connect()")
        payload = _wrap_parcel(command)
        info = self._client.publish(topic, payload=payload, qos=qos, retain=retain)
        info.wait_for_publish(timeout=2.0)

    def publish_broadcast(self, command: dict[str, Any]) -> None:
        """發給所有感測器（全體廣播 topic）。"""
        self.publish_command(self.BROADCAST_TOPIC, command)

    def publish_to_kind(self, sensor_kind: str, command: dict[str, Any]) -> None:
        """只發給特定種類的感測器（pedestrian_flow / visual / digital / facility_event）。"""
        self.publish_command(self.KIND_TOPIC_FMT.format(kind=sensor_kind), command)

    # ------------------------------------------------------------------
    # 高階：事件注入
    # ------------------------------------------------------------------
    def inject_event(
        self,
        kind: str,
        target: Any,
        *,
        ttl_samples: int = 3,
        payload: Optional[dict[str, Any]] = None,
        broadcast: bool = False,
    ) -> dict[str, Any]:
        """
        通用注入：依 kind 選擇對應感測器類型 topic；可選 broadcast 走全體廣播。

        回傳實際送出的命令 dict（含 routing topic），供 logger 記錄。
        """
        command = {
            "command": "inject_event",
            "kind": kind,
            "target": target,
            "ttl_samples": int(ttl_samples),
        }
        if payload:
            command["payload"] = payload

        if broadcast:
            self.publish_broadcast(command)
            command["_routed_to"] = self.BROADCAST_TOPIC
            return command

        sensor_kind = _EVENT_KIND_TO_SENSOR_KIND.get(kind)
        if sensor_kind is None:
            # 不認得的 kind 直接 broadcast
            self.publish_broadcast(command)
            command["_routed_to"] = self.BROADCAST_TOPIC
        else:
            topic = self.KIND_TOPIC_FMT.format(kind=sensor_kind)
            self.publish_command(topic, command)
            command["_routed_to"] = topic
        return command

    def inject_crowd_congestion(
        self, zone: str | list[str], *, ttl_samples: int = 3
    ) -> dict[str, Any]:
        return self.inject_event(EVENT_KIND_CROWD, zone, ttl_samples=ttl_samples)

    def inject_crowd_clear(
        self, zone: str | list[str], *, ttl_samples: int = 2
    ) -> dict[str, Any]:
        return self.inject_event("crowd_clear", zone, ttl_samples=ttl_samples)

    def inject_area_closure(
        self, zone: str, *, ttl_samples: int = 3
    ) -> dict[str, Any]:
        return self.inject_event(EVENT_KIND_CLOSURE, zone, ttl_samples=ttl_samples)

    def inject_area_reopen(self, zone: str, *, ttl_samples: int = 2) -> dict[str, Any]:
        return self.inject_event("area_reopen", zone, ttl_samples=ttl_samples)

    def inject_route_detour(
        self,
        from_node: str,
        to_node: str,
        *,
        obstacle_type: str = "barrier",
        ttl_samples: int = 3,
    ) -> dict[str, Any]:
        return self.inject_event(
            EVENT_KIND_DETOUR,
            {"from": from_node, "to": to_node},
            ttl_samples=ttl_samples,
            payload={"obstacle_type": obstacle_type},
        )

    def inject_route_clear(
        self, from_node: str, to_node: str, *, ttl_samples: int = 2
    ) -> dict[str, Any]:
        return self.inject_event(
            "route_clear",
            {"from": from_node, "to": to_node},
            ttl_samples=ttl_samples,
        )

    # ------------------------------------------------------------------
    # 控制：頻率／隨機參數
    # ------------------------------------------------------------------
    def set_random_param(
        self, sensor_kind: str, name: str, value: Any
    ) -> dict[str, Any]:
        """調整指定種類感測器的隨機參數。"""
        command = {
            "command": "set_random_param",
            "name": name,
            "value": value,
        }
        self.publish_to_kind(sensor_kind, command)
        return command

    def set_contact_probability(self, sensor_kind: str, probability: float) -> dict[str, Any]:
        command = {
            "command": "set_contact_probability",
            "probability": float(probability),
        }
        self.publish_to_kind(sensor_kind, command)
        return command

    def set_interval(self, sensor_kind: str, interval_sec: float) -> dict[str, Any]:
        command = {"command": "set_interval", "interval": float(interval_sec)}
        self.publish_to_kind(sensor_kind, command)
        return command

    def pause_all(self) -> None:
        self.publish_broadcast({"command": "pause"})

    def resume_all(self) -> None:
        self.publish_broadcast({"command": "resume"})

    def clear_all_overrides(self) -> None:
        self.publish_broadcast({"command": "clear_overrides"})

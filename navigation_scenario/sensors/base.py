"""
Navigation Scenario 感測器代理基礎類別（繼承 AgentFlow Agent）。

各子類別模擬不同感測維度；在輪詢週期內以 contact_probability 隨機決定
是否向 BlackboardAgent 發送 observe / write 指令以更新 Blackboard KG。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Optional
import random
import threading
import time
import uuid

from agentflow.core.agent import Agent

from src.log_helper import init_logging
from src.blackboard.agent import BlackboardAgent

logger = init_logging()

UpdateKind = Literal["observe", "write"]


# Public MQTT control topics
SENSORS_BROADCAST_TOPIC = "navigation_scenario.sensors.control"


def sensors_kind_topic(sensor_kind: str) -> str:
    """同類感測器共用的廣播 topic（例如 navigation_scenario.sensors.visual.control）。"""
    return f"navigation_scenario.sensors.{sensor_kind}.control"


@dataclass
class EventOverride:
    """暫存的事件覆寫指令；下一次 sample_update 會被消費。

    Attributes:
        kind: 事件種類（如 crowd_congestion / area_closure / route_detour ...）
        target: 目標識別碼，型別由感測器自行決定：
                - Zone 名稱字串 / 字串列表
                - dict（如 {"from": "B_RB1", "to": "B_RB2"}）
                - Booth id 字串
        payload: 額外資料（如 obstacle_type）
        ttl_samples: 此覆寫可被消費的次數；0 = 已過期
    """

    kind: str
    target: Any = None
    payload: dict[str, Any] = field(default_factory=dict)
    ttl_samples: int = 1


@dataclass
class SensorUpdate:
    """要送交 BlackboardAgent 的單次更新。

    write 模式可選擇單一語句（`cypher` + `params`）或多語句
    （`statements`）。多語句模式可在一次 sample 內以原子方式
    更新多個節點，且皆採 idempotent MATCH+SET，不會堆積歷史節點。

    日誌欄位：
    - `info_text`：給 INFO 級別的中文白話描述（操作員視角）。
    - `summary`：簡短一行，當 info_text 缺省時備用。
    """

    kind: UpdateKind
    # observe
    observation: dict[str, Any] | None = None
    # write（單一）
    cypher: str | None = None
    params: dict[str, Any] | None = None
    # write（多筆）：[(cypher, params), ...]
    statements: list[tuple[str, dict[str, Any]]] | None = None
    # 日誌
    summary: str = ""
    info_text: str = ""


class ScenarioSensorAgent(Agent, ABC):
    """
    展場導航測試用感測器代理基礎類別。

    子類別實作：
      - sensor_kind(): 感測器類型識別
      - sample_update(): 依感測特性產生一次更新（或 None）
      - HANDLED_EVENT_KINDS: 此感測器可處理的事件 kind

    控制介面（MQTT）：
      - `navigation_scenario.sensor.<sensor_id>.control`：單一實例
      - `navigation_scenario.sensors.<sensor_kind>.control`：同類所有實例
      - `navigation_scenario.sensors.control`：所有感測器
    支援 commands：status / set_contact_probability / set_interval /
        sample_now / inject_event / clear_overrides / pause / resume
    """

    BLACKBOARD_TOPIC = BlackboardAgent.CONTROL_TOPIC
    CONTROL_TOPIC_PREFIX = "navigation_scenario.sensor"
    BROADCAST_TOPIC = SENSORS_BROADCAST_TOPIC

    HANDLED_EVENT_KINDS: ClassVar[tuple[str, ...]] = ()

    def __init__(
        self,
        name: str,
        agent_config: dict[str, Any],
        *,
        poll_interval_sec: float = 2.0,
        poll_jitter_sec: float = 0.5,
        contact_probability: float = 0.5,
        rng: random.Random | None = None,
    ):
        self._sensor_id = f"{name}_{uuid.uuid4().hex[:8]}"
        self._poll_interval = max(0.2, float(poll_interval_sec))
        self._poll_jitter = max(0.0, float(poll_jitter_sec))
        self._contact_probability = max(0.0, min(1.0, float(contact_probability)))
        self._rng = rng or random.Random()
        self._running = False
        self._paused = False
        self._thread: threading.Thread | None = None
        self._updates_sent = 0

        # Event override 佇列（被 sample_update 消費）
        self._overrides: list[EventOverride] = []
        self._overrides_lock = threading.Lock()

        super().__init__(name, agent_config)

    @property
    def sensor_id(self) -> str:
        return self._sensor_id

    @property
    def contact_probability(self) -> float:
        return self._contact_probability

    @property
    def poll_interval_sec(self) -> float:
        return self._poll_interval

    @property
    def control_topic(self) -> str:
        return f"{self.CONTROL_TOPIC_PREFIX}.{self._sensor_id}.control"

    @property
    def kind_control_topic(self) -> str:
        return sensors_kind_topic(self.sensor_kind())

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def pending_override_count(self) -> int:
        with self._overrides_lock:
            return len(self._overrides)

    @abstractmethod
    def sensor_kind(self) -> str:
        """感測器種類（pedestrian_flow / facility_event / visual / digital）。"""

    @abstractmethod
    def sample_update(self) -> SensorUpdate | None:
        """
        產生一次感測更新。

        由子類別依各自感測值特性（隨機分布、狀態持久化等）實作。
        若本次無有效讀數可回傳 None。
        """

    def on_connected(self) -> None:
        logger.info(
            "ScenarioSensorAgent connected: %s kind=%s id=%s p_contact=%.2f handled=%s",
            self.name,
            self.sensor_kind(),
            self._sensor_id,
            self._contact_probability,
            ",".join(self.HANDLED_EVENT_KINDS) or "-",
        )
        # 三層控制 topic：自己、同類、全體
        self.subscribe(self.control_topic, "dict", self._handle_control)
        self.subscribe(self.kind_control_topic, "dict", self._handle_control)
        self.subscribe(self.BROADCAST_TOPIC, "dict", self._handle_control)
        self._start_loop()

    def on_disconnected(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("ScenarioSensorAgent disconnected: %s", self._sensor_id)

    def _start_loop(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._sense_loop,
            daemon=True,
            name=f"SensorLoop-{self.sensor_kind()}",
        )
        self._thread.start()

    def _sense_loop(self) -> None:
        while self._running:
            try:
                if not self._paused and self._rng.random() < self._contact_probability:
                    update = self.sample_update()
                    if update is not None:
                        self._send_to_blackboard(update)
            except Exception as e:
                logger.warning(
                    "ScenarioSensorAgent loop error (%s): %s",
                    self.sensor_kind(),
                    e,
                )
            delay = self._poll_interval + self._rng.uniform(0, self._poll_jitter)
            time.sleep(delay)

    # ------------------------------------------------------------------
    # Event override（給 sample_update 消費）
    # ------------------------------------------------------------------
    def apply_event_override(
        self,
        kind: str,
        target: Any,
        payload: dict[str, Any] | None = None,
        ttl_samples: int = 1,
    ) -> bool:
        """
        將事件覆寫加入佇列。下一次 sample_update 會優先採用。

        若 `kind` 不在 `HANDLED_EVENT_KINDS` 中，回傳 False；否則 True。
        """
        if kind not in self.HANDLED_EVENT_KINDS:
            return False
        with self._overrides_lock:
            self._overrides.append(
                EventOverride(
                    kind=kind,
                    target=target,
                    payload=payload or {},
                    ttl_samples=max(1, int(ttl_samples)),
                )
            )
        return True

    def _pop_overrides(self) -> list[EventOverride]:
        """
        取出本次 sample_update 可用的 overrides；ttl 為 1 的會被移除，
        ttl > 1 的會減 1 後保留供下次使用。
        """
        with self._overrides_lock:
            current = list(self._overrides)
            remaining: list[EventOverride] = []
            for ov in self._overrides:
                if ov.ttl_samples > 1:
                    remaining.append(
                        EventOverride(
                            kind=ov.kind,
                            target=ov.target,
                            payload=ov.payload,
                            ttl_samples=ov.ttl_samples - 1,
                        )
                    )
            self._overrides = remaining
            return current

    def _send_to_blackboard(self, update: SensorUpdate) -> bool:
        try:
            statements: list[tuple[str, dict[str, Any]]] = []

            if update.kind == "observe":
                if not update.observation:
                    return False
                payload = {
                    "command": "observe",
                    "observation": update.observation,
                    "requester_id": self._sensor_id,
                }
                self.publish(self.BLACKBOARD_TOPIC, payload)
            elif update.kind == "write":
                if update.statements:
                    statements.extend(update.statements)
                elif update.cypher:
                    statements.append((update.cypher, update.params or {}))
                if not statements:
                    return False
                for cy, ps in statements:
                    self.publish(
                        self.BLACKBOARD_TOPIC,
                        {
                            "command": "write",
                            "cypher": cy,
                            "params": ps,
                            "requester_id": self._sensor_id,
                        },
                    )
            else:
                return False

            self._updates_sent += 1

            # INFO：白話描述「做了什麼」
            info_text = update.info_text or update.summary or update.kind
            logger.info("[%s] %s", self._sensor_label(), info_text)

            # VERBOSE：印實際 Cypher 與參數
            if update.kind == "observe":
                self._log_verbose_observe(update.observation or {})
            else:
                for idx, (cy, ps) in enumerate(statements, start=1):
                    self._log_verbose_cypher(idx, len(statements), cy, ps)

            return True
        except Exception as e:
            logger.warning(
                "[%s] Blackboard publish failed: %s",
                self._sensor_label(),
                e,
            )
            return False

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------
    def _sensor_label(self) -> str:
        return f"sensor:{self.sensor_kind()}"

    def _log_verbose_cypher(
        self, idx: int, total: int, cypher: str, params: dict[str, Any]
    ) -> None:
        cypher_oneline = " ".join(cypher.strip().split())
        prefix = f"[{self._sensor_label()}] cypher {idx}/{total}"
        logger.verbose("%s: %s", prefix, cypher_oneline)
        logger.verbose("%s params: %s", prefix, self._format_params(params))

    def _log_verbose_observe(self, observation: dict[str, Any]) -> None:
        obs_type = observation.get("observation_type", "?")
        entities = observation.get("entities", [])
        prefix = f"[{self._sensor_label()}] observe"
        logger.verbose(
            "%s type=%s entities=%d saliency=%s",
            prefix,
            obs_type,
            len(entities),
            observation.get("saliency"),
        )
        for i, e in enumerate(entities, start=1):
            logger.verbose(
                "%s entity %d: type=%s label=%s id=%s props=%s",
                prefix,
                i,
                e.get("entity_type"),
                e.get("label"),
                e.get("entity_id"),
                e.get("properties"),
            )

    @staticmethod
    def _format_params(params: dict[str, Any]) -> str:
        # 對 rows / statements 等長列表做截斷，避免 VERBOSE 過長
        snippet: dict[str, Any] = {}
        for k, v in params.items():
            if isinstance(v, list) and len(v) > 3:
                snippet[k] = v[:3] + [f"...(+{len(v) - 3} more)"]
            else:
                snippet[k] = v
        return repr(snippet)

    def _handle_control(self, topic: str, payload: Any) -> dict[str, Any]:
        if hasattr(payload, "content"):
            data = payload.content if isinstance(payload.content, dict) else {}
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {}

        command = data.get("command", "")

        # 子類別可優先處理自家指令（回 None 表示交給 base 處理）
        sub_result = self._handle_subclass_command(command, data)
        if sub_result is not None:
            return sub_result

        if command == "status":
            return {
                "ok": True,
                "sensor_id": self._sensor_id,
                "sensor_kind": self.sensor_kind(),
                "running": self._running,
                "paused": self._paused,
                "poll_interval_sec": self._poll_interval,
                "contact_probability": self._contact_probability,
                "updates_sent": self._updates_sent,
                "pending_overrides": self.pending_override_count,
                "handled_event_kinds": list(self.HANDLED_EVENT_KINDS),
                "random_params": self.get_random_params(),
            }

        if command == "set_contact_probability":
            p = float(data.get("probability", self._contact_probability))
            self._contact_probability = max(0.0, min(1.0, p))
            return {"ok": True, "contact_probability": self._contact_probability}

        if command == "set_interval":
            self._poll_interval = max(0.2, float(data.get("interval", self._poll_interval)))
            return {"ok": True, "poll_interval_sec": self._poll_interval}

        if command == "sample_now":
            update = self.sample_update()
            if update is None:
                return {"ok": True, "sent": False, "message": "no update sampled"}
            sent = self._send_to_blackboard(update)
            return {"ok": True, "sent": sent, "kind": update.kind, "summary": update.summary}

        if command == "pause":
            self._paused = True
            logger.info("[%s] 已暫停隨機輪詢（事件覆寫仍可被注入）", self._sensor_label())
            return {"ok": True, "paused": True}

        if command == "resume":
            self._paused = False
            logger.info("[%s] 已恢復隨機輪詢", self._sensor_label())
            return {"ok": True, "paused": False}

        if command == "clear_overrides":
            with self._overrides_lock:
                cleared = len(self._overrides)
                self._overrides.clear()
            logger.info("[%s] 清除 %d 筆 event override", self._sensor_label(), cleared)
            return {"ok": True, "cleared": cleared}

        if command == "inject_event":
            return self._on_inject_event(data)

        if command == "set_random_param":
            name = str(data.get("name", ""))
            value = data.get("value")
            ok, new_val, err = self.set_random_param(name, value)
            if not ok:
                return {"ok": False, "error": err or f"Unknown random param: {name}"}
            logger.info(
                "[%s] 已調整隨機參數 %s = %s", self._sensor_label(), name, new_val
            )
            return {"ok": True, "name": name, "value": new_val}

        if command == "get_random_params":
            return {"ok": True, "random_params": self.get_random_params()}

        return {"ok": False, "error": f"Unknown command: {command}"}

    # ------------------------------------------------------------------
    # 子類別可選擇覆寫的鉤子
    # ------------------------------------------------------------------
    def _handle_subclass_command(
        self, command: str, data: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """
        子類別自家命令處理：回 None 表示交給 base 處理共通命令。
        """
        return None

    def get_random_params(self) -> dict[str, Any]:
        """
        回傳目前可被 set_random_param 動態調整的隨機參數狀態（給 status 用）。
        子類別請覆寫。
        """
        return {}

    def set_random_param(
        self, name: str, value: Any
    ) -> tuple[bool, Any, Optional[str]]:
        """
        嘗試設定隨機參數。
        回傳 (ok, new_value, error_msg)。子類別請覆寫。
        """
        return (False, None, f"Sensor '{self.sensor_kind()}' does not expose '{name}'")

    # ------------------------------------------------------------------
    # 動態事件注入
    # ------------------------------------------------------------------
    def _on_inject_event(self, data: dict[str, Any]) -> dict[str, Any]:
        kind = data.get("kind", "")
        target = data.get("target")
        ttl = int(data.get("ttl_samples", 1))
        payload = data.get("payload") or {}

        if kind not in self.HANDLED_EVENT_KINDS:
            return {
                "ok": True,
                "accepted": False,
                "skipped": True,
                "sensor_kind": self.sensor_kind(),
                "reason": f"kind '{kind}' not handled by {self.sensor_kind()}",
            }

        accepted = self.apply_event_override(kind, target, payload, ttl)
        if not accepted:
            return {
                "ok": False,
                "accepted": False,
                "sensor_kind": self.sensor_kind(),
                "reason": "override rejected",
            }

        # 立即取一份更新並送進 Blackboard，讓事件立刻生效
        update = self.sample_update()
        sent = False
        if update is not None:
            sent = self._send_to_blackboard(update)

        logger.info(
            "[%s] 事件注入：kind=%s target=%s ttl=%d sent=%s",
            self._sensor_label(),
            kind,
            target,
            ttl,
            sent,
        )

        return {
            "ok": True,
            "accepted": True,
            "sensor_kind": self.sensor_kind(),
            "sensor_id": self._sensor_id,
            "kind": kind,
            "target": target,
            "ttl_samples": ttl,
            "sent": sent,
        }

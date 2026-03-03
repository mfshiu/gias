# src/observer/base.py
"""
ObserverAgent 基礎類別

觀察者代理是系統的感官觸角，負責：
1. 精準感知：專注於特定感知領域，過濾具有「資訊增益」的特徵
2. 語義映射：將觀察到的現象映射為實體與關係
3. 重要性評估：判斷觀察結果的重要性（1-5 級）
4. 封裝交付：將處理後的資訊封裝成標準格式，發送給黑板代理

運作準則：
- 客觀紀錄：只描述「發生了什麼」，不進行主觀推論
- 時戳標註：每筆觀察必須包含精確的發生時間
- 置信度宣告：針對觀察結果給予 0.0 到 1.0 的置信度
- 職責解耦：不負責寫入資料庫或執行動作
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional
import threading
import time
import uuid

from agentflow.core.agent import Agent

from src.app_helper import get_agent_config
from src.log_helper import init_logging
from src.blackboard.agent import BlackboardAgent

from .observation import (
    Observation,
    ObservationType,
    SaliencyLevel,
    Entity,
    Relation,
)

logger = init_logging()


class SaliencyFilter:
    """
    重要性過濾器
    
    決定觀察結果是否值得上報。
    維護歷史狀態以偵測變化幅度。
    """
    
    def __init__(
        self,
        min_saliency: SaliencyLevel = SaliencyLevel.MODERATE,
        cooldown_sec: float = 1.0,
    ):
        self.min_saliency = min_saliency
        self.cooldown_sec = cooldown_sec
        self._last_report_time: dict[str, float] = {}
        self._last_state: dict[str, Any] = {}
        self._lock = threading.Lock()

    def should_report(self, observation: Observation, state_key: str = "") -> bool:
        """
        判斷是否應該上報此觀察結果
        
        考慮因素：
        1. 重要性是否達到門檻
        2. 是否在冷卻時間內
        3. 狀態是否有實質變化
        """
        if observation.saliency < self.min_saliency:
            logger.debug("Observation filtered: saliency %d < %d", 
                        observation.saliency, self.min_saliency)
            return False
        
        key = state_key or observation.observer_id
        current_time = time.time()
        
        with self._lock:
            last_time = self._last_report_time.get(key, 0)
            if current_time - last_time < self.cooldown_sec:
                logger.debug("Observation filtered: cooldown (%.1fs remaining)",
                            self.cooldown_sec - (current_time - last_time))
                return False
            
            self._last_report_time[key] = current_time
        
        return True

    def update_state(self, key: str, state: Any) -> bool:
        """
        更新狀態並檢查是否有變化
        
        Returns:
            True if state changed, False otherwise
        """
        with self._lock:
            old_state = self._last_state.get(key)
            if old_state == state:
                return False
            self._last_state[key] = state
            return True

    def compute_saliency(
        self,
        *,
        entity_count: int = 0,
        relation_count: int = 0,
        state_changed: bool = False,
        confidence: float = 1.0,
        urgency_keywords: list[str] | None = None,
        raw_description: str = "",
    ) -> SaliencyLevel:
        """
        根據觀察特徵計算重要性等級
        
        Args:
            entity_count: 觀察到的實體數量
            relation_count: 觀察到的關係數量
            state_changed: 狀態是否有變化
            confidence: 觀察置信度
            urgency_keywords: 緊急關鍵字列表
            raw_description: 原始描述
        
        Returns:
            計算出的重要性等級
        """
        score = 1.0
        
        if entity_count > 0:
            score += min(entity_count * 0.3, 1.5)
        if relation_count > 0:
            score += min(relation_count * 0.2, 1.0)
        if state_changed:
            score += 1.0
        
        score *= confidence
        
        if urgency_keywords and raw_description:
            desc_lower = raw_description.lower()
            for keyword in urgency_keywords:
                if keyword.lower() in desc_lower:
                    score += 1.0
                    break
        
        if score >= 4.5:
            return SaliencyLevel.CRITICAL
        elif score >= 3.5:
            return SaliencyLevel.HIGH
        elif score >= 2.5:
            return SaliencyLevel.MODERATE
        elif score >= 1.5:
            return SaliencyLevel.LOW
        else:
            return SaliencyLevel.TRIVIAL


class ObserverAgent(Agent, ABC):
    """
    觀察者代理基礎類別
    
    子類別需實作：
    - observe(): 執行觀察並回傳 Observation（若無顯著觀察則回傳 None）
    - get_observation_type(): 回傳此觀察者的感知類型
    
    可選覆寫：
    - on_observation(): 處理觀察結果（預設為提交給黑板代理）
    - get_urgency_keywords(): 回傳緊急關鍵字列表
    """
    
    BLACKBOARD_TOPIC = "blackboard.control"
    OBSERVER_TOPIC_PREFIX = "observer"

    def __init__(
        self,
        name: str,
        agent_config: dict[str, Any],
        *,
        poll_interval_sec: float = 1.0,
        min_saliency: SaliencyLevel = SaliencyLevel.MODERATE,
        cooldown_sec: float = 0.5,
    ):
        self._observer_id = f"{name}_{uuid.uuid4().hex[:8]}"
        self._poll_interval = poll_interval_sec
        self._running = False
        self._observe_thread: Optional[threading.Thread] = None
        
        self.saliency_filter = SaliencyFilter(
            min_saliency=min_saliency,
            cooldown_sec=cooldown_sec,
        )
        
        super().__init__(name, agent_config)

    @property
    def observer_id(self) -> str:
        return self._observer_id

    @abstractmethod
    def get_observation_type(self) -> ObservationType:
        """回傳此觀察者的感知類型"""
        ...

    @abstractmethod
    def observe(self) -> Optional[Observation]:
        """
        執行一次觀察
        
        Returns:
            Observation if significant observation made, None otherwise
        """
        ...

    def get_urgency_keywords(self) -> list[str]:
        """回傳緊急關鍵字列表（用於重要性評估）"""
        return ["緊急", "警告", "錯誤", "異常", "危險", "urgent", "error", "warning", "critical"]

    def create_observation(
        self,
        *,
        entities: list[Entity] | None = None,
        relations: list[Relation] | None = None,
        raw_description: str = "",
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
        saliency: SaliencyLevel | None = None,
    ) -> Observation:
        """
        建立觀察結果的便利方法
        
        若未指定 saliency，會自動計算
        """
        entities = entities or []
        relations = relations or []
        
        if saliency is None:
            saliency = self.saliency_filter.compute_saliency(
                entity_count=len(entities),
                relation_count=len(relations),
                confidence=confidence,
                urgency_keywords=self.get_urgency_keywords(),
                raw_description=raw_description,
            )
        
        return Observation.create(
            observer_id=self.observer_id,
            observation_type=self.get_observation_type(),
            saliency=saliency,
            confidence=confidence,
            entities=entities,
            relations=relations,
            raw_description=raw_description,
            metadata=metadata,
        )

    def on_connected(self) -> None:
        """Agent 連線後啟動觀察循環"""
        logger.info("ObserverAgent connected: %s (type=%s)", 
                    self.observer_id, self.get_observation_type().value)
        
        self.subscribe(
            f"{self.OBSERVER_TOPIC_PREFIX}.{self.observer_id}.control",
            "dict",
            self._handle_control,
        )
        
        self._start_observation_loop()

    def on_disconnected(self) -> None:
        """Agent 斷線時停止觀察循環"""
        self._stop_observation_loop()
        logger.info("ObserverAgent disconnected: %s", self.observer_id)

    def _start_observation_loop(self) -> None:
        """啟動觀察循環"""
        if self._running:
            return
        
        self._running = True
        self._observe_thread = threading.Thread(
            target=self._observation_loop,
            daemon=True,
            name=f"ObserverLoop-{self.observer_id}",
        )
        self._observe_thread.start()
        logger.info("ObserverAgent observation loop started (interval=%.1fs)", 
                    self._poll_interval)

    def _stop_observation_loop(self) -> None:
        """停止觀察循環"""
        self._running = False
        if self._observe_thread and self._observe_thread.is_alive():
            self._observe_thread.join(timeout=2.0)

    def _observation_loop(self) -> None:
        """觀察主循環"""
        while self._running:
            try:
                observation = self.observe()
                
                if observation and self.saliency_filter.should_report(observation):
                    self.on_observation(observation)
                
            except Exception as e:
                logger.warning("ObserverAgent observation error: %s", e)
            
            time.sleep(self._poll_interval)

    def on_observation(self, observation: Observation) -> None:
        """
        處理觀察結果
        
        預設行為：提交給黑板代理
        子類別可覆寫以實作自訂處理
        """
        self.submit_to_blackboard(observation)

    def submit_to_blackboard(self, observation: Observation) -> bool:
        """
        將觀察結果提交給黑板代理
        
        Returns:
            True if submission successful, False otherwise
        """
        payload = {
            "command": "observe",
            "observation": observation.to_dict(),
            "requester_id": self.observer_id,
        }
        
        try:
            self.publish(self.BLACKBOARD_TOPIC, payload)
            logger.info("ObserverAgent submitted observation: saliency=%d, entities=%d",
                       observation.saliency, len(observation.entities))
            return True
        except Exception as e:
            logger.warning("ObserverAgent submit failed: %s", e)
            return False

    def _handle_control(self, topic: str, payload: Any) -> dict[str, Any]:
        """處理控制訊息"""
        if hasattr(payload, "content"):
            data = payload.content if isinstance(payload.content, dict) else {}
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {}
        
        command = data.get("command", "")
        
        if command == "set_saliency":
            level = data.get("level", 3)
            self.saliency_filter.min_saliency = SaliencyLevel(level)
            return {"ok": True, "min_saliency": level}
        
        if command == "set_interval":
            interval = data.get("interval", 1.0)
            self._poll_interval = max(0.1, float(interval))
            return {"ok": True, "poll_interval": self._poll_interval}
        
        if command == "status":
            return {
                "ok": True,
                "observer_id": self.observer_id,
                "observation_type": self.get_observation_type().value,
                "running": self._running,
                "poll_interval": self._poll_interval,
                "min_saliency": int(self.saliency_filter.min_saliency),
            }
        
        if command == "observe_now":
            observation = self.observe()
            if observation:
                self.on_observation(observation)
                return {"ok": True, "observation_id": observation.observation_id}
            return {"ok": True, "observation_id": None, "message": "No significant observation"}
        
        return {"ok": False, "error": f"Unknown command: {command}"}

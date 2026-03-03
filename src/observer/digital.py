# src/observer/digital.py
"""
數位觀察者代理

負責捕捉數位維度的資訊：
- API 狀態碼與回應時間
- 數據波動趨勢
- 系統指標（CPU、記憶體、磁碟）
- 異常用量偵測

使用場景：
- 系統健康監控
- API 服務監測
- 資源使用追蹤
- 異常行為偵測
"""

from __future__ import annotations

from typing import Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import time

from src.app_helper import get_agent_config
from src.log_helper import init_logging

from .base import ObserverAgent
from .observation import (
    Observation,
    ObservationType,
    SaliencyLevel,
    Entity,
    Relation,
)

logger = init_logging()


class MetricType(Enum):
    """指標類型"""
    COUNTER = "counter"         # 計數器（只增不減）
    GAUGE = "gauge"             # 量表（可增可減）
    HISTOGRAM = "histogram"     # 直方圖
    RATE = "rate"               # 速率


class HealthStatus(Enum):
    """健康狀態"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class TrendDirection(Enum):
    """趨勢方向"""
    STABLE = "stable"
    RISING = "rising"
    FALLING = "falling"
    VOLATILE = "volatile"


@dataclass
class Metric:
    """數位指標"""
    name: str
    value: float
    metric_type: MetricType
    unit: str = ""
    tags: dict[str, str] | None = None
    timestamp: float | None = None

    def to_entity(self) -> Entity:
        props = {
            "value": self.value,
            "metric_type": self.metric_type.value,
            "unit": self.unit,
            "timestamp": self.timestamp or time.time(),
        }
        if self.tags:
            props["tags"] = self.tags
        
        return Entity(
            entity_type="Metric",
            label=self.name,
            entity_id=f"metric_{self.name}",
            properties=props,
            confidence=1.0,
        )


@dataclass
class ApiStatus:
    """API 狀態"""
    endpoint: str
    status_code: int
    response_time_ms: float
    health: HealthStatus
    error_message: Optional[str] = None

    def to_entity(self) -> Entity:
        props = {
            "status_code": self.status_code,
            "response_time_ms": self.response_time_ms,
            "health": self.health.value,
        }
        if self.error_message:
            props["error_message"] = self.error_message
        
        is_ok = 200 <= self.status_code < 400
        confidence = 1.0 if is_ok else 0.9
        
        return Entity(
            entity_type="ApiEndpoint",
            label=self.endpoint,
            entity_id=f"api_{self.endpoint.replace('/', '_')}",
            properties=props,
            confidence=confidence,
        )


class DigitalObserver(ObserverAgent):
    """
    數位觀察者代理
    
    監控數位系統指標、API 狀態、資源使用等，
    偵測異常波動與趨勢變化。
    
    子類別需實作：
    - collect_metrics(): 收集系統指標
    - check_apis(): 檢查 API 狀態
    """
    
    RESPONSE_TIME_WARN_MS = 1000.0
    RESPONSE_TIME_CRITICAL_MS = 3000.0
    
    CPU_WARN_PERCENT = 80.0
    CPU_CRITICAL_PERCENT = 95.0
    
    MEMORY_WARN_PERCENT = 80.0
    MEMORY_CRITICAL_PERCENT = 95.0

    def __init__(
        self,
        name: str = "digital_observer",
        agent_config: dict[str, Any] | None = None,
        *,
        poll_interval_sec: float = 5.0,
        trend_window_size: int = 10,
    ):
        agent_config = agent_config or get_agent_config()
        self._trend_window_size = trend_window_size
        self._metric_history: dict[str, list[float]] = {}
        self._last_api_status: dict[str, HealthStatus] = {}
        
        super().__init__(
            name=name,
            agent_config=agent_config,
            poll_interval_sec=poll_interval_sec,
            min_saliency=SaliencyLevel.LOW,
            cooldown_sec=1.0,
        )

    def get_observation_type(self) -> ObservationType:
        return ObservationType.DIGITAL

    def get_urgency_keywords(self) -> list[str]:
        return ["error", "timeout", "exception", "critical", "failed", "down"]

    def collect_metrics(self) -> list[Metric]:
        """
        收集系統指標
        
        子類別應覆寫此方法以實作實際的指標收集邏輯。
        預設回傳空列表（模擬模式）。
        
        Returns:
            收集到的指標列表
        """
        return []

    def check_apis(self) -> list[ApiStatus]:
        """
        檢查 API 狀態
        
        子類別應覆寫此方法以實作實際的 API 檢查邏輯。
        預設回傳空列表（模擬模式）。
        
        Returns:
            API 狀態列表
        """
        return []

    def compute_trend(self, metric_name: str, current_value: float) -> TrendDirection:
        """計算指標趨勢"""
        history = self._metric_history.setdefault(metric_name, [])
        history.append(current_value)
        
        if len(history) > self._trend_window_size:
            history.pop(0)
        
        if len(history) < 3:
            return TrendDirection.STABLE
        
        diffs = [history[i+1] - history[i] for i in range(len(history)-1)]
        avg_diff = sum(diffs) / len(diffs)
        
        variance = sum((d - avg_diff) ** 2 for d in diffs) / len(diffs)
        
        if variance > (avg_diff ** 2) * 2:
            return TrendDirection.VOLATILE
        elif avg_diff > 0.1 * (sum(history) / len(history)):
            return TrendDirection.RISING
        elif avg_diff < -0.1 * (sum(history) / len(history)):
            return TrendDirection.FALLING
        else:
            return TrendDirection.STABLE

    def assess_metric_saliency(self, metric: Metric, trend: TrendDirection) -> SaliencyLevel:
        """評估指標的重要性"""
        name_lower = metric.name.lower()
        value = metric.value
        
        if "cpu" in name_lower:
            if value >= self.CPU_CRITICAL_PERCENT:
                return SaliencyLevel.CRITICAL
            elif value >= self.CPU_WARN_PERCENT:
                return SaliencyLevel.HIGH
        
        if "memory" in name_lower or "mem" in name_lower:
            if value >= self.MEMORY_CRITICAL_PERCENT:
                return SaliencyLevel.CRITICAL
            elif value >= self.MEMORY_WARN_PERCENT:
                return SaliencyLevel.HIGH
        
        if trend == TrendDirection.VOLATILE:
            return SaliencyLevel.HIGH
        elif trend in (TrendDirection.RISING, TrendDirection.FALLING):
            return SaliencyLevel.MODERATE
        
        return SaliencyLevel.LOW

    def assess_api_saliency(self, api: ApiStatus) -> SaliencyLevel:
        """評估 API 狀態的重要性"""
        if api.health == HealthStatus.UNHEALTHY:
            return SaliencyLevel.CRITICAL
        
        if api.health == HealthStatus.DEGRADED:
            return SaliencyLevel.HIGH
        
        if api.response_time_ms >= self.RESPONSE_TIME_CRITICAL_MS:
            return SaliencyLevel.CRITICAL
        elif api.response_time_ms >= self.RESPONSE_TIME_WARN_MS:
            return SaliencyLevel.HIGH
        
        if api.status_code >= 500:
            return SaliencyLevel.CRITICAL
        elif api.status_code >= 400:
            return SaliencyLevel.HIGH
        
        last_status = self._last_api_status.get(api.endpoint)
        if last_status and last_status != api.health:
            self._last_api_status[api.endpoint] = api.health
            return SaliencyLevel.HIGH
        
        self._last_api_status[api.endpoint] = api.health
        return SaliencyLevel.LOW

    def observe(self) -> Optional[Observation]:
        """執行數位觀察"""
        metrics = self.collect_metrics()
        apis = self.check_apis()
        
        if not metrics and not apis:
            return None
        
        entities: list[Entity] = []
        max_saliency = SaliencyLevel.TRIVIAL
        issues: list[str] = []
        
        for metric in metrics:
            trend = self.compute_trend(metric.name, metric.value)
            saliency = self.assess_metric_saliency(metric, trend)
            
            entity = metric.to_entity()
            entity.properties["trend"] = trend.value
            entities.append(entity)
            
            if saliency > max_saliency:
                max_saliency = saliency
            
            if saliency >= SaliencyLevel.HIGH:
                issues.append(f"{metric.name}={metric.value}{metric.unit} ({trend.value})")
        
        for api in apis:
            saliency = self.assess_api_saliency(api)
            entities.append(api.to_entity())
            
            if saliency > max_saliency:
                max_saliency = saliency
            
            if saliency >= SaliencyLevel.HIGH:
                issues.append(f"{api.endpoint}: {api.status_code} ({api.health.value})")
        
        if max_saliency < self.saliency_filter.min_saliency:
            return None
        
        if issues:
            raw_description = f"偵測到問題: {'; '.join(issues)}"
        else:
            raw_description = f"系統狀態正常: {len(metrics)} 個指標, {len(apis)} 個 API"
        
        return self.create_observation(
            entities=entities,
            raw_description=raw_description,
            confidence=1.0,
            saliency=max_saliency,
            metadata={
                "metric_count": len(metrics),
                "api_count": len(apis),
                "issues": issues,
                "observation_time": time.time(),
            },
        )


class SimulatedDigitalObserver(DigitalObserver):
    """
    模擬數位觀察者（用於測試）
    
    可注入模擬的指標和 API 狀態。
    """
    
    def __init__(
        self,
        name: str = "simulated_digital_observer",
        agent_config: dict[str, Any] | None = None,
        **kwargs,
    ):
        self._simulated_metrics: list[Metric] = []
        self._simulated_apis: list[ApiStatus] = []
        super().__init__(name=name, agent_config=agent_config, **kwargs)

    def set_simulated_metrics(self, metrics: list[Metric]) -> None:
        """設定模擬的指標"""
        self._simulated_metrics = metrics

    def set_simulated_apis(self, apis: list[ApiStatus]) -> None:
        """設定模擬的 API 狀態"""
        self._simulated_apis = apis

    def collect_metrics(self) -> list[Metric]:
        return self._simulated_metrics

    def check_apis(self) -> list[ApiStatus]:
        return self._simulated_apis

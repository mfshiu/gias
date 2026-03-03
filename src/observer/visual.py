# src/observer/visual.py
"""
視覺觀察者代理

負責捕捉視覺維度的資訊：
- 物體偵測與識別
- 空間位移與方向
- 顏色與外觀特徵
- OCR 文字識別

使用場景：
- 展場攝影機監控訪客動態
- 機器人視覺感知環境
- 展品狀態監測
"""

from __future__ import annotations

from typing import Any, Callable, Optional
from dataclasses import dataclass
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


@dataclass
class BoundingBox:
    """物體邊界框"""
    x: float  # 左上角 x
    y: float  # 左上角 y
    width: float
    height: float
    
    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)


@dataclass
class DetectedObject:
    """偵測到的物體"""
    label: str
    confidence: float
    bbox: BoundingBox
    object_id: Optional[str] = None
    attributes: dict[str, Any] | None = None

    def to_entity(self) -> Entity:
        props = {
            "bbox": self.bbox.to_dict(),
            **(self.attributes or {}),
        }
        return Entity(
            entity_type="Object",
            label=self.label,
            entity_id=self.object_id,
            properties=props,
            confidence=self.confidence,
        )


class VisualObserver(ObserverAgent):
    """
    視覺觀察者代理
    
    監控視覺輸入（攝影機、影像串流等），
    偵測物體、人員、位置變化等視覺事件。
    
    子類別需實作：
    - detect_objects(): 執行物體偵測
    """
    
    def __init__(
        self,
        name: str = "visual_observer",
        agent_config: dict[str, Any] | None = None,
        *,
        poll_interval_sec: float = 0.5,
        min_confidence: float = 0.5,
        track_positions: bool = True,
    ):
        agent_config = agent_config or get_agent_config()
        self._min_confidence = min_confidence
        self._track_positions = track_positions
        self._last_positions: dict[str, tuple[float, float]] = {}
        self._movement_threshold = 20.0  # 像素
        
        super().__init__(
            name=name,
            agent_config=agent_config,
            poll_interval_sec=poll_interval_sec,
            min_saliency=SaliencyLevel.MODERATE,
        )

    def get_observation_type(self) -> ObservationType:
        return ObservationType.VISUAL

    def get_urgency_keywords(self) -> list[str]:
        return ["人", "person", "fire", "smoke", "fall", "跌倒", "火災", "煙霧"]

    def detect_objects(self) -> list[DetectedObject]:
        """
        執行物體偵測
        
        子類別應覆寫此方法以實作實際的視覺偵測邏輯。
        預設回傳空列表（模擬模式）。
        
        Returns:
            偵測到的物體列表
        """
        return []

    def observe(self) -> Optional[Observation]:
        """執行視覺觀察"""
        detected = self.detect_objects()
        
        detected = [obj for obj in detected if obj.confidence >= self._min_confidence]
        
        if not detected:
            return None
        
        entities: list[Entity] = []
        relations: list[Relation] = []
        state_changed = False
        
        for obj in detected:
            entity = obj.to_entity()
            entities.append(entity)
            
            if self._track_positions and obj.object_id:
                current_pos = obj.bbox.center
                last_pos = self._last_positions.get(obj.object_id)
                
                if last_pos:
                    dx = current_pos[0] - last_pos[0]
                    dy = current_pos[1] - last_pos[1]
                    distance = (dx**2 + dy**2) ** 0.5
                    
                    if distance > self._movement_threshold:
                        state_changed = True
                        entity.properties["movement"] = {
                            "dx": dx,
                            "dy": dy,
                            "distance": distance,
                        }
                
                self._last_positions[obj.object_id] = current_pos
        
        saliency = self.saliency_filter.compute_saliency(
            entity_count=len(entities),
            relation_count=len(relations),
            state_changed=state_changed,
            confidence=min(obj.confidence for obj in detected) if detected else 1.0,
        )
        
        labels = [obj.label for obj in detected]
        raw_description = f"偵測到 {len(detected)} 個物體: {', '.join(labels)}"
        
        return self.create_observation(
            entities=entities,
            relations=relations,
            raw_description=raw_description,
            confidence=sum(obj.confidence for obj in detected) / len(detected),
            saliency=saliency,
            metadata={
                "frame_timestamp": time.time(),
                "object_count": len(detected),
            },
        )


class SimulatedVisualObserver(VisualObserver):
    """
    模擬視覺觀察者（用於測試）
    
    可注入模擬的偵測結果。
    """
    
    def __init__(
        self,
        name: str = "simulated_visual_observer",
        agent_config: dict[str, Any] | None = None,
        **kwargs,
    ):
        self._simulated_objects: list[DetectedObject] = []
        super().__init__(name=name, agent_config=agent_config, **kwargs)

    def set_simulated_objects(self, objects: list[DetectedObject]) -> None:
        """設定模擬的偵測結果"""
        self._simulated_objects = objects

    def detect_objects(self) -> list[DetectedObject]:
        return self._simulated_objects

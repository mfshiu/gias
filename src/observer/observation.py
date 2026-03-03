# src/observer/observation.py
"""
觀察結果資料結構

定義觀察者代理產生的結構化觀察結果，包含：
- Entity: 觀察到的實體
- Relation: 實體間的關係
- Observation: 完整的觀察結果 Payload
- SaliencyLevel: 重要性等級 (1-5)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Optional
import json
import uuid


class SaliencyLevel(IntEnum):
    """
    重要性等級（1-5）
    
    用於判斷觀察結果是否值得上報。
    若環境無顯著變化，觀察者應保持靜默。
    """
    TRIVIAL = 1       # 瑣碎：可忽略的微小變化
    LOW = 2           # 低：輕微變化，可能有用
    MODERATE = 3      # 中等：值得注意的變化
    HIGH = 4          # 高：重要變化，需要關注
    CRITICAL = 5      # 關鍵：緊急事件，需立即處理


class ObservationType(Enum):
    """觀察類型（感知維度）"""
    VISUAL = "visual"           # 視覺：物體、空間、顏色、OCR
    AUDIO = "audio"             # 聽覺：語音、聲調、環境音
    DIGITAL = "digital"         # 數位：API、數據、指標
    SYSTEM = "system"           # 系統：內部狀態、異常
    COMPOSITE = "composite"     # 複合：多模態融合


@dataclass
class Entity:
    """
    觀察到的實體
    
    Attributes:
        entity_type: 實體類型（如 Person, Object, Zone）
        entity_id: 實體識別碼（若可識別）
        label: 實體標籤/名稱
        properties: 實體屬性
        confidence: 識別置信度 (0.0-1.0)
    """
    entity_type: str
    label: str
    entity_id: Optional[str] = None
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    def __post_init__(self):
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "label": self.label,
            "properties": self.properties,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        return cls(
            entity_type=data["entity_type"],
            entity_id=data.get("entity_id"),
            label=data["label"],
            properties=data.get("properties", {}),
            confidence=data.get("confidence", 1.0),
        )


@dataclass
class Relation:
    """
    實體間的關係
    
    Attributes:
        relation_type: 關係類型（如 LOCATED_AT, INTERACTS_WITH）
        source: 來源實體
        target: 目標實體
        properties: 關係屬性
        confidence: 關係置信度 (0.0-1.0)
    """
    relation_type: str
    source: Entity
    target: Entity
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    def __post_init__(self):
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_type": self.relation_type,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "properties": self.properties,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Relation":
        return cls(
            relation_type=data["relation_type"],
            source=Entity.from_dict(data["source"]),
            target=Entity.from_dict(data["target"]),
            properties=data.get("properties", {}),
            confidence=data.get("confidence", 1.0),
        )


@dataclass
class Observation:
    """
    完整的觀察結果 Payload
    
    這是觀察者代理提交給黑板代理的標準格式。
    
    Attributes:
        observation_id: 觀察識別碼
        observer_id: 觀察者代理 ID
        observation_type: 觀察類型（感知維度）
        timestamp: 觀察發生時間（ISO 格式）
        saliency: 重要性等級 (1-5)
        confidence: 整體置信度 (0.0-1.0)
        entities: 觀察到的實體列表
        relations: 實體間的關係列表
        raw_description: 原始描述（自然語言）
        metadata: 額外元資料
    """
    observation_id: str
    observer_id: str
    observation_type: ObservationType
    timestamp: str
    saliency: SaliencyLevel
    confidence: float
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    raw_description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {self.confidence}")

    @classmethod
    def create(
        cls,
        *,
        observer_id: str,
        observation_type: ObservationType,
        saliency: SaliencyLevel,
        confidence: float = 1.0,
        entities: list[Entity] | None = None,
        relations: list[Relation] | None = None,
        raw_description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "Observation":
        """建立新的觀察結果"""
        return cls(
            observation_id=str(uuid.uuid4()),
            observer_id=observer_id,
            observation_type=observation_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            saliency=saliency,
            confidence=confidence,
            entities=entities or [],
            relations=relations or [],
            raw_description=raw_description,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """轉換為字典格式（用於傳輸）"""
        return {
            "observation_id": self.observation_id,
            "observer_id": self.observer_id,
            "observation_type": self.observation_type.value,
            "timestamp": self.timestamp,
            "saliency": int(self.saliency),
            "confidence": self.confidence,
            "entities": [e.to_dict() for e in self.entities],
            "relations": [r.to_dict() for r in self.relations],
            "raw_description": self.raw_description,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """序列化為 JSON"""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Observation":
        """從字典還原"""
        return cls(
            observation_id=data["observation_id"],
            observer_id=data["observer_id"],
            observation_type=ObservationType(data["observation_type"]),
            timestamp=data["timestamp"],
            saliency=SaliencyLevel(data["saliency"]),
            confidence=data.get("confidence", 1.0),
            entities=[Entity.from_dict(e) for e in data.get("entities", [])],
            relations=[Relation.from_dict(r) for r in data.get("relations", [])],
            raw_description=data.get("raw_description", ""),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "Observation":
        """從 JSON 還原"""
        return cls.from_dict(json.loads(json_str))

    def is_significant(self, min_saliency: SaliencyLevel = SaliencyLevel.MODERATE) -> bool:
        """判斷觀察結果是否達到上報門檻"""
        return self.saliency >= min_saliency

    def merge_entities(self, other: "Observation") -> None:
        """合併另一個觀察結果的實體（用於多模態融合）"""
        existing_ids = {e.entity_id for e in self.entities if e.entity_id}
        for entity in other.entities:
            if entity.entity_id not in existing_ids:
                self.entities.append(entity)
                if entity.entity_id:
                    existing_ids.add(entity.entity_id)

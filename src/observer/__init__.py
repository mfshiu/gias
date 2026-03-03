# src/observer/__init__.py
"""
Observer 模組：感知與轉譯代理

負責監控環境中的特定模態訊號，並將其轉化為黑板系統可理解的結構化事實。

核心組件：
- Observation: 觀察結果資料結構
- SaliencyLevel: 重要性等級
- ObserverAgent: 觀察者代理基類
- VisualObserver: 視覺觀察者
- AudioObserver: 聽覺觀察者
- DigitalObserver: 數位觀察者
"""

from .observation import (
    Observation,
    SaliencyLevel,
    Entity,
    Relation,
    ObservationType,
)
from .base import ObserverAgent
from .visual import VisualObserver
from .audio import AudioObserver
from .digital import DigitalObserver

__all__ = [
    "Observation",
    "SaliencyLevel",
    "Entity",
    "Relation",
    "ObservationType",
    "ObserverAgent",
    "VisualObserver",
    "AudioObserver",
    "DigitalObserver",
]

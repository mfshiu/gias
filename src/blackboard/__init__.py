# src/blackboard/__init__.py
"""
Blackboard Pub/Sub 機制模組

提供：
- BlackboardTopic: Topic 命名空間解析
- BlackboardEvent: 事件 Payload 結構
- BlackboardWatcher: 監控 KG 變動
- SubscriptionManager: 訂閱管理（精確/模糊）
- BlackboardAgent: 黑板代理主程式
"""

from .topic import BlackboardTopic, TopicPattern
from .event import BlackboardEvent, ChangeAction
from .watcher import BlackboardWatcher
from .subscription import SubscriptionManager
from .agent import BlackboardAgent

__all__ = [
    "BlackboardTopic",
    "TopicPattern",
    "BlackboardEvent",
    "ChangeAction",
    "BlackboardWatcher",
    "SubscriptionManager",
    "BlackboardAgent",
]

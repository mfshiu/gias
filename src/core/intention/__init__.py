"""
意圖管理：可跨時間追蹤的 Intention、生命週期、排程（優先序與搶占）與持久化。

- model   : Intention、IntentionStatus（生命週期）、MQTT topic 常數
- store   : InMemoryIntentionStore、BlackboardIntentionStore（寫成 Blackboard 的 Intention 節點）
- manager : IntentionManager（一次處理一個意圖；高優先序可搶占；取消；狀態通知）
"""

from .manager import IntentionManager, plan_steps
from .model import (
    TERMINAL_STATUSES,
    TOPIC_INTENTION_CANCEL,
    TOPIC_INTENTION_QUERY,
    TOPIC_INTENTION_STATUS,
    TOPIC_INTENTION_SUBMIT,
    Intention,
    IntentionStatus,
    InvalidTransition,
)
from .store import BlackboardIntentionStore, InMemoryIntentionStore, IntentionStore

__all__ = [
    "BlackboardIntentionStore",
    "InMemoryIntentionStore",
    "Intention",
    "IntentionManager",
    "IntentionStatus",
    "IntentionStore",
    "InvalidTransition",
    "TERMINAL_STATUSES",
    "TOPIC_INTENTION_CANCEL",
    "TOPIC_INTENTION_QUERY",
    "TOPIC_INTENTION_STATUS",
    "TOPIC_INTENTION_SUBMIT",
    "plan_steps",
]

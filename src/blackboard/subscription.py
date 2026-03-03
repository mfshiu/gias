# src/blackboard/subscription.py
"""
Subscription Manager：管理精確訂閱與模糊訂閱

支援：
1. 精確訂閱：Agent/GuideBot_01/status
2. 模糊訂閱：Agent/*/status（所有 Agent 的 status）
             */CURRENT_STATE/*（任何 CURRENT_STATE 關係）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
import threading
import uuid

from .topic import TopicPattern, BlackboardTopic
from .event import BlackboardEvent


SubscriptionCallback = Callable[[BlackboardEvent], None]


@dataclass
class Subscription:
    """單一訂閱"""
    id: str
    pattern: TopicPattern
    callback: SubscriptionCallback
    subscriber_id: str
    ignore_self: bool = True

    def matches(self, topic: str) -> bool:
        return self.pattern.matches(topic)

    def should_notify(self, event: BlackboardEvent) -> bool:
        if not self.matches(event.topic):
            return False
        if self.ignore_self and event.is_from(self.subscriber_id):
            return False
        return True


class SubscriptionManager:
    """
    訂閱管理器

    - 支援精確與模糊訂閱
    - 自動過濾自己發出的事件（防止死循環）
    - 執行緒安全
    """

    def __init__(self):
        self._subscriptions: dict[str, Subscription] = {}
        self._lock = threading.RLock()

    def subscribe(
        self,
        pattern: str,
        callback: SubscriptionCallback,
        subscriber_id: str,
        *,
        ignore_self: bool = True,
    ) -> str:
        """
        訂閱指定 pattern

        Args:
            pattern: Topic 模式（可含萬用字元 *）
            callback: 收到事件時的回呼函式
            subscriber_id: 訂閱者的 Agent ID（用於過濾自己的事件）
            ignore_self: 是否忽略自己發出的變更（預設 True，防止死循環）

        Returns:
            訂閱 ID（可用於取消訂閱）
        """
        sub_id = str(uuid.uuid4())
        topic_pattern = TopicPattern.create(pattern)
        subscription = Subscription(
            id=sub_id,
            pattern=topic_pattern,
            callback=callback,
            subscriber_id=subscriber_id,
            ignore_self=ignore_self,
        )
        with self._lock:
            self._subscriptions[sub_id] = subscription
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        with self._lock:
            if subscription_id in self._subscriptions:
                del self._subscriptions[subscription_id]
                return True
            return False

    def unsubscribe_all(self, subscriber_id: str) -> int:
        """取消指定訂閱者的所有訂閱"""
        count = 0
        with self._lock:
            to_remove = [
                sid for sid, sub in self._subscriptions.items()
                if sub.subscriber_id == subscriber_id
            ]
            for sid in to_remove:
                del self._subscriptions[sid]
                count += 1
        return count

    def dispatch(self, event: BlackboardEvent) -> int:
        """
        將事件分發給所有匹配的訂閱者

        Returns:
            通知的訂閱者數量
        """
        notified = 0
        with self._lock:
            subscriptions = list(self._subscriptions.values())

        for sub in subscriptions:
            if sub.should_notify(event):
                try:
                    sub.callback(event)
                    notified += 1
                except Exception:
                    pass
        return notified

    def get_subscriptions(self, subscriber_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            subs = self._subscriptions.values()
            if subscriber_id:
                subs = [s for s in subs if s.subscriber_id == subscriber_id]
            return [
                {"id": s.id, "pattern": str(s.pattern), "subscriber_id": s.subscriber_id}
                for s in subs
            ]

    def count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

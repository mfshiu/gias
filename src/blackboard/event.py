# src/blackboard/event.py
"""
Blackboard 事件 Payload 結構

每個事件包含：
- topic: 變動的 Topic（例：Agent/GuideBot_01/status）
- action: Create / Update / Delete
- old_value: 變更前的值（Create 時為 None）
- new_value: 變更後的值（Delete 時為 None）
- timestamp: ISO 格式時間戳
- origin_id: 發起變更的 Agent ID（用於防止死循環）
- metadata: 額外資訊（node_id、rel_id 等）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import json


class ChangeAction(Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class BlackboardEvent:
    """黑板變動事件"""
    topic: str
    action: ChangeAction
    old_value: Any
    new_value: Any
    timestamp: str
    origin_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        topic: str,
        action: ChangeAction,
        old_value: Any = None,
        new_value: Any = None,
        origin_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> "BlackboardEvent":
        return cls(
            topic=topic,
            action=action,
            old_value=old_value,
            new_value=new_value,
            timestamp=datetime.now(timezone.utc).isoformat(),
            origin_id=origin_id,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "action": self.action.value,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "timestamp": self.timestamp,
            "origin_id": self.origin_id,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BlackboardEvent":
        return cls(
            topic=data["topic"],
            action=ChangeAction(data["action"]),
            old_value=data.get("old_value"),
            new_value=data.get("new_value"),
            timestamp=data["timestamp"],
            origin_id=data["origin_id"],
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "BlackboardEvent":
        return cls.from_dict(json.loads(json_str))

    def is_from(self, agent_id: str) -> bool:
        """檢查事件是否由指定 Agent 發起（用於防止死循環）"""
        return self.origin_id == agent_id

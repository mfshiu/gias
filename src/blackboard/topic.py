# src/blackboard/topic.py
"""
Blackboard Topic 命名空間

支援格式：
1. 節點屬性變動：<Label>/<Name>/<Property>
   例：Agent/GuideBot_01/status, Zone/AI_Tech_Area/crowd_level

2. 關係變動：<SourceLabel>/<Relationship>/<TargetLabel>
   例：Agent/CURRENT_POSITION/POI, Task/HAS_STATUS/State

3. 模糊訂閱（使用萬用字元 *）：
   - Agent/*/status       → 所有 Agent 的 status 屬性
   - Agent/*              → 所有 Agent 的任何變動
   - */CURRENT_STATE/*    → 任何節點的 CURRENT_STATE 關係
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TopicType(Enum):
    NODE_PROPERTY = "node_property"
    RELATIONSHIP = "relationship"


@dataclass(frozen=True)
class BlackboardTopic:
    """解析後的 Topic 結構"""
    raw: str
    topic_type: TopicType
    label: str
    name_or_rel: str
    property_or_target: str

    @classmethod
    def parse(cls, topic_str: str) -> "BlackboardTopic":
        parts = topic_str.strip().split("/")
        if len(parts) != 3:
            raise ValueError(f"Invalid topic format: {topic_str}. Expected <A>/<B>/<C>")

        label, middle, last = parts[0], parts[1], parts[2]

        if cls._is_relationship_name(middle):
            return cls(
                raw=topic_str,
                topic_type=TopicType.RELATIONSHIP,
                label=label,
                name_or_rel=middle,
                property_or_target=last,
            )
        else:
            return cls(
                raw=topic_str,
                topic_type=TopicType.NODE_PROPERTY,
                label=label,
                name_or_rel=middle,
                property_or_target=last,
            )

    @staticmethod
    def _is_relationship_name(name: str) -> bool:
        if name == "*":
            return False
        return name.isupper() or "_" in name.upper() == name

    @classmethod
    def for_node_property(cls, label: str, name: str, prop: str) -> "BlackboardTopic":
        return cls(
            raw=f"{label}/{name}/{prop}",
            topic_type=TopicType.NODE_PROPERTY,
            label=label,
            name_or_rel=name,
            property_or_target=prop,
        )

    @classmethod
    def for_relationship(cls, source_label: str, rel_type: str, target_label: str) -> "BlackboardTopic":
        return cls(
            raw=f"{source_label}/{rel_type}/{target_label}",
            topic_type=TopicType.RELATIONSHIP,
            label=source_label,
            name_or_rel=rel_type,
            property_or_target=target_label,
        )

    def __str__(self) -> str:
        return self.raw


@dataclass(frozen=True)
class TopicPattern:
    """支援萬用字元的訂閱模式"""
    pattern: str
    _regex: re.Pattern

    @classmethod
    def create(cls, pattern: str) -> "TopicPattern":
        regex_str = "^" + re.escape(pattern).replace(r"\*", r"[^/]+") + "$"
        return cls(pattern=pattern, _regex=re.compile(regex_str))

    def matches(self, topic: str | BlackboardTopic) -> bool:
        topic_str = str(topic) if isinstance(topic, BlackboardTopic) else topic
        return bool(self._regex.match(topic_str))

    def is_exact(self) -> bool:
        return "*" not in self.pattern

    def __str__(self) -> str:
        return self.pattern

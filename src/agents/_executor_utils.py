# src/agents/_executor_utils.py
"""Agent 共用：topic 常數、解析 payload、組裝請求/結果"""

from __future__ import annotations

import json
from typing import Any

# 與 InfoAgent / NavigationAgent 訂閱的 topic 一致
TOPIC_INFO_REQUEST = "info.request"
TOPIC_NAVIGATION_REQUEST = "navigation.request"

# topic 簡稱 -> 完整 request topic
_TOPIC_MAP = {
    "info": TOPIC_INFO_REQUEST,
    "navigation": TOPIC_NAVIGATION_REQUEST,
}


def resolve_request_topic(topic: str | None) -> str:
    """將 topic 簡稱或 DB 值解析為實際的 request topic（與 InfoAgent/NavigationAgent 訂閱一致）。"""
    if not topic:
        return TOPIC_INFO_REQUEST  # fallback
    t = (topic or "").strip().lower()
    if t in _TOPIC_MAP:
        return _TOPIC_MAP[t]
    if ".request" in t:
        return topic.strip()  # 已是完整 topic，保留原樣
    return TOPIC_INFO_REQUEST


def build_action_payload(
    task: str,
    params: dict[str, Any],
    action_id: Any = None,
    intent: str = "",
) -> dict[str, Any]:
    """組裝 action 請求 payload（與 parse_action_payload 對應）。"""
    return {
        "task": task,
        "params": params or {},
        "action_id": action_id,
        "intent": intent or "",
    }


def parse_action_payload(pcl: Any) -> dict[str, Any]:
    """從 parcel 解析 payload：{task, params, action_id, intent}"""
    content = getattr(pcl, "content", pcl) if pcl is not None else {}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {}
    return content if isinstance(content, dict) else {}


def build_result(task: str, message: str, action_id: Any, intent: str) -> dict[str, Any]:
    """組裝執行結果"""
    return {
        "ok": True,
        "task": task,
        "message": message,
        "simulated": True,
        "action_id": action_id,
        "intent": intent,
    }

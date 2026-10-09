# src/blackboard/client.py
"""
黑板查詢客戶端

供行動者代理（InfoAgent、NavigationAgent 等）經由黑板代理存取環境資訊。
使用 AgentFlow 的 publish_sync 向 blackboard.control 發送 query 命令。
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .agent import BlackboardAgent

if TYPE_CHECKING:
    from agentflow.core.agent import Agent


def _control(agent: "Agent", payload: dict[str, Any], *, timeout: float) -> dict[str, Any] | None:
    """送出 blackboard.control 命令；黑板代理沒回應或回 ok=False 時回傳 None。"""
    try:
        pcl = agent.publish_sync(BlackboardAgent.CONTROL_TOPIC, payload, timeout=timeout)
        resp = getattr(pcl, "content", None) if pcl else None
        if isinstance(resp, dict) and resp.get("ok"):
            return resp
    except Exception:
        pass
    return None


def try_query_blackboard(
    agent: "Agent",
    cypher: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
) -> list[dict[str, Any]] | None:
    """同 query_blackboard，但查詢失敗時回傳 None（可與「查無資料」的空列表區分）。"""
    resp = _control(agent, {
        "command": "query",
        "cypher": cypher,
        "params": params or {},
        "requester_id": getattr(agent, "agent_id", "unknown"),
    }, timeout=timeout)
    return resp.get("rows", []) if resp is not None else None


def query_blackboard(
    agent: "Agent",
    cypher: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    """
    經由黑板代理查詢 Blackboard KG

    Args:
        agent: 具 publish_sync 的 Agent 實例（如 InfoAgent、NavigationAgent）
        cypher: Cypher 查詢語句
        params: 查詢參數
        timeout: 逾時秒數

    Returns:
        查詢結果 rows，若失敗則回傳空列表
    """
    return try_query_blackboard(agent, cypher, params, timeout=timeout) or []


def subscriber_topic(requester_id: str) -> str:
    """黑板代理把訂閱事件轉發到的 MQTT topic。"""
    return f"{BlackboardAgent.SUBSCRIBER_TOPIC_PREFIX}.{requester_id}"


def subscribe_blackboard(
    agent: "Agent",
    pattern: str,
    *,
    requester_id: str,
    timeout: float = 2.0,
) -> str | None:
    """向黑板代理訂閱 topic pattern（例："Zone/*/*"）。

    事件會發佈到 subscriber_topic(requester_id)，呼叫端需自行 subscribe 該 topic。
    成功回傳 subscription_id；黑板代理沒回應或拒絕時回傳 None。
    """
    resp = _control(agent, {
        "command": "subscribe",
        "pattern": pattern,
        "requester_id": requester_id,
    }, timeout=timeout)
    return resp.get("subscription_id") if resp is not None else None


def unsubscribe_blackboard(agent: "Agent", *, requester_id: str, timeout: float = 2.0) -> bool:
    """取消 requester_id 在黑板代理上的所有訂閱。"""
    return _control(agent, {"command": "unsubscribe", "requester_id": requester_id}, timeout=timeout) is not None


def get_crowd_hotspots(agent: "Agent") -> list[dict[str, Any]]:
    """
    取得擁擠熱點（各 Zone 人潮狀態）

    Returns:
        [{"zone": "AI_Tech_Area", "crowd_status": "Crowded"}, ...]
    """
    cypher = """
    MATCH (z:Zone)-[:CURRENT_STATE]->(s:State)
    RETURN z.name AS zone, s.status_name AS crowd_status
    """
    rows = query_blackboard(agent, cypher)
    return [{"zone": r.get("zone"), "crowd_status": r.get("crowd_status")} for r in rows]


def get_open_booths(agent: "Agent") -> list[dict[str, Any]]:
    """
    取得開放中的展位

    Returns:
        [{"id": "B_A1", "exhibitor": "TechCorp AI"}, ...]
    """
    cypher = """
    MATCH (b:Booth)
    WHERE b.status = 'open' OR b.status IS NULL
    RETURN b.id AS id, b.exhibitor AS exhibitor
    """
    rows = query_blackboard(agent, cypher)
    return [{"id": r.get("id"), "exhibitor": r.get("exhibitor")} for r in rows if r.get("id")]

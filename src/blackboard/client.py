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
    payload = {
        "command": "query",
        "cypher": cypher,
        "params": params or {},
        "requester_id": getattr(agent, "agent_id", "unknown"),
    }
    try:
        pcl = agent.publish_sync(BlackboardAgent.CONTROL_TOPIC, payload, timeout=timeout)
        resp = getattr(pcl, "content", None) if pcl else None
        if isinstance(resp, dict) and resp.get("ok"):
            return resp.get("rows", [])
    except Exception:
        pass
    return []


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

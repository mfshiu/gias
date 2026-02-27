# src/agents/info_agent.py
"""
InfoAgent：提供資訊類 action（ExplainExhibit, AnswerFAQ, LocateFacility, ProvideSchedule, RecommendExhibits, CrowdStatus）

訂閱：info.request

執行：python -m src.agents.info_agent
"""

from __future__ import annotations

import time
from typing import Any

from agentflow.core.agent import Agent

from src.app_helper import get_agent_config
from src.log_helper import init_logging
from src.agents._executor_utils import parse_action_payload, build_result

logger = init_logging()

TOPIC = "info.request"


def _simulate_progress(steps: list[str], delay: float = 0.6) -> None:
    """模擬處理過程，逐步顯示進度"""
    for i, msg in enumerate(steps, 1):
        print(f"  [InfoAgent] {i}/{len(steps)} {msg}", flush=True)
        logger.info("InfoAgent progress: %s", msg)
        time.sleep(delay)


def _execute(task: str, params: dict[str, Any]) -> str:
    """模擬執行資訊類 task，含處理過程與延遲"""
    if task == "ExplainExhibit":
        target = params.get("target_name", "?")
        _simulate_progress([
            f"查詢展品「{target}」資料...",
            "載入多媒體說明...",
            "整理介紹內容...",
        ])
        return f"已介紹展品 {target} 的內容與特色。"
    if task == "AnswerFAQ":
        q = params.get("question", "?")
        _simulate_progress([
            f"搜尋 FAQ：{q[:20]}{'...' if len(str(q)) > 20 else ''}",
            "比對知識庫...",
            "產生回覆...",
        ])
        return f"已回覆 FAQ：{q}"
    if task == "LocateFacility":
        ft = params.get("facility_type", "?")
        _simulate_progress([
            f"查詢 {ft} 設施位置...",
            "取得樓層與動線...",
            "規劃最近路線...",
        ])
        return f"已找到 {ft} 設施位置。"
    if task == "ProvideSchedule":
        _simulate_progress([
            "載入活動行事曆...",
            "篩選今日場次...",
            "整理時間與地點...",
        ])
        return "已提供活動時間與地點資訊。"
    if task == "RecommendExhibits":
        interests = params.get("interests", [])
        _simulate_progress([
            f"依興趣 {interests} 搜尋展區...",
            "評估熱門度與人潮...",
            "排序推薦清單...",
        ])
        return f"已根據興趣 {interests} 推薦展區。"
    if task == "CrowdStatus":
        _simulate_progress([
            "讀取各展區感測資料...",
            "計算即時人潮密度...",
            "產生人潮報告...",
        ])
        return "目前各展區人潮狀況正常。"
    _simulate_progress(["處理中...", "完成"])
    return f"已提供 {task} 相關資訊。"


class InfoAgent(Agent):
    """提供資訊類 action"""

    def __init__(self, agent_config: dict[str, Any]):
        super().__init__("info_agent.gias", agent_config)

    def on_connected(self) -> None:
        logger.info("InfoAgent subscribing: %s", TOPIC)
        self.subscribe(TOPIC, "dict", self._handle)

    def _handle(self, topic: str, pcl: Any) -> dict[str, Any]:
        payload = parse_action_payload(pcl)
        task = payload.get("task", "Unknown")
        params = payload.get("params") or {}
        action_id = payload.get("action_id")
        intent = payload.get("intent", "")

        print(f"\n[InfoAgent] 收到請求: task={task} params={params}", flush=True)
        logger.info("InfoAgent received: task=%s params=%s", task, params)

        message = _execute(task, params)

        print(f"  [InfoAgent] ✓ 完成: {message}", flush=True)
        logger.info("InfoAgent result: %s", str(message))
        result = build_result(task, message, action_id, intent)
        return result


if __name__ == "__main__":
    agent = InfoAgent(agent_config=get_agent_config())
    agent.start_process()
    from src.app_helper import wait_agent

    wait_agent(agent)

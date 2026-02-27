# src/agents/navigation_agent.py
"""
NavigationAgent：實際帶領/導航類 action（LocateExhibit, SuggestRoute, ExplainDirections, NavigationAssistance）

訂閱：navigation.request

執行：python -m src.agents.navigation_agent
"""

from __future__ import annotations

import time
from typing import Any

from agentflow.core.agent import Agent

from src.app_helper import get_agent_config
from src.log_helper import init_logging
from src.agents._executor_utils import parse_action_payload, build_result

logger = init_logging()

TOPIC = "navigation.request"


def _simulate_progress(steps: list[str], delay: float = 0.6) -> None:
    """模擬處理過程，逐步顯示進度"""
    for i, msg in enumerate(steps, 1):
        print(f"  [NavigationAgent] {i}/{len(steps)} {msg}", flush=True)
        logger.info("NavigationAgent progress: %s", msg)
        time.sleep(delay)


def _execute(task: str, params: dict[str, Any]) -> str:
    """模擬執行導航類 task，含處理過程與延遲"""
    if task == "LocateExhibit":
        target = params.get("target_name", "?")
        loc = params.get("current_location", "?")
        _simulate_progress([
            f"定位起點：{loc}...",
            f"搜尋目標「{target}」...",
            "計算最短路徑...",
            "產生導航指引...",
        ])
        return f"已規劃從 {loc} 前往 {target} 的路線，請跟我來。"
    if task == "SuggestRoute":
        loc = params.get("current_location", "?")
        _simulate_progress([
            f"取得您的位置：{loc}...",
            "分析展區動線與人潮...",
            "優化參觀順序...",
            "產生建議路線...",
        ])
        return f"已根據您的位置 {loc} 規劃建議參觀路線。"
    if task == "ExplainDirections":
        dest = params.get("destination", "?")
        loc = params.get("current_location", "?")
        _simulate_progress([
            f"從 {loc} 到 {dest}...",
            "查詢展場平面圖...",
            "標示轉彎點與地標...",
        ])
        return f"從 {loc} 前往 {dest}：直走約 50 公尺，左轉後第二個攤位即為目標。"
    if task == "NavigationAssistance":
        dest = params.get("destination", "?")
        loc = params.get("current_location", "?")
        _simulate_progress([
            f"啟動導航：{loc} → {dest}...",
            "持續追蹤位置...",
            "準備語音指引...",
        ])
        return f"導航中：從 {loc} 前往 {dest}，請跟隨指示前進。"
    _simulate_progress(["處理中...", "完成"])
    return f"已執行導航任務 {task}。"


class NavigationAgent(Agent):
    """實際帶領/導航類 action"""

    def __init__(self, agent_config: dict[str, Any]):
        super().__init__("navigation_agent.gias", agent_config)

    def on_connected(self) -> None:
        logger.info("NavigationAgent subscribing: %s", TOPIC)
        self.subscribe(TOPIC, "dict", self._handle)

    def _handle(self, topic: str, pcl: Any) -> dict[str, Any]:
        payload = parse_action_payload(pcl)
        task = payload.get("task", "Unknown")
        params = payload.get("params") or {}
        action_id = payload.get("action_id")
        intent = payload.get("intent", "")

        print(f"\n[NavigationAgent] 收到請求: task={task} params={params}", flush=True)
        logger.info("NavigationAgent received: task=%s params=%s", task, params)

        message = _execute(task, params)

        print(f"  [NavigationAgent] ✓ 完成: {message}", flush=True)
        logger.info("NavigationAgent result: %s", str(message))
        result = build_result(task, message, action_id, intent)
        return result


if __name__ == "__main__":
    agent = NavigationAgent(agent_config=get_agent_config())
    agent.start_process()
    from src.app_helper import wait_agent

    wait_agent(agent)

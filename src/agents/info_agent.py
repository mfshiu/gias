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
from src.blackboard.client import get_crowd_hotspots, get_open_booths

logger = init_logging()

TOPIC = "info.request"


def _simulate_progress(steps: list[str], delay: float = 0.2) -> None:
    """模擬處理過程，逐步顯示進度"""
    for i, msg in enumerate(steps, 1):
        print(f"  [InfoAgent] {i}/{len(steps)} {msg}", flush=True)
        logger.info("InfoAgent progress: %s", msg)
        time.sleep(delay)


def _execute(agent: "InfoAgent", task: str, params: dict[str, Any]) -> str:
    """模擬執行資訊類 task，含處理過程與延遲；部分 task 會查詢黑板以反映觀察者影響"""
    if task == "CrowdStatus":
        _simulate_progress([
            "向黑板查詢觀察者回報的人潮資料...",
            "整合各 Zone 即時狀態...",
            "產生人潮報告...",
        ])
        hotspots = get_crowd_hotspots(agent)
        if hotspots:
            crowded = [h for h in hotspots if h.get("crowd_status") == "Crowded"]
            normal = [h for h in hotspots if h.get("crowd_status") == "Normal"]
            sparse = [h for h in hotspots if h.get("crowd_status") == "Sparse"]
            lines = []
            if crowded:
                zones = ", ".join(h.get("zone", "?") for h in crowded)
                lines.append(f"【黑板觀察】擁擠區: {zones}")
            if normal:
                zones = ", ".join(h.get("zone", "?") for h in normal)
                lines.append(f"【黑板觀察】正常區: {zones}")
            if sparse:
                zones = ", ".join(h.get("zone", "?") for h in sparse)
                lines.append(f"【黑板觀察】稀疏區: {zones}")
            return "；".join(lines) if lines else "目前各展區人潮狀況正常。"
        return "無法取得黑板人潮資料（請確認觀察者與黑板代理已啟動）。"
    if task == "RecommendExhibits":
        interests = params.get("interests", [])
        _simulate_progress([
            f"依興趣 {interests} 搜尋展區...",
            "向黑板查詢人潮與展位狀態...",
            "排序推薦清單...",
        ])
        hotspots = get_crowd_hotspots(agent)
        booths = get_open_booths(agent)
        avoid = [h.get("zone") for h in (hotspots or []) if h.get("crowd_status") == "Crowded"]
        rec = [b.get("exhibitor") or b.get("id") for b in (booths or []) if b.get("id")]
        msg = f"已根據興趣 {interests} 推薦展區。"
        if avoid:
            msg += f" 【黑板觀察】建議避開擁擠區: {', '.join(avoid)}"
        if rec:
            msg += f" 開放展位: {', '.join(rec)}"
        return msg
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

        message = _execute(self, task, params)

        print(f"  [InfoAgent] ✓ 完成: {message}", flush=True)
        logger.info("InfoAgent result: %s", str(message))
        result = build_result(task, message, action_id, intent)
        return result


if __name__ == "__main__":
    agent = InfoAgent(agent_config=get_agent_config())
    agent.start_process()
    from src.app_helper import wait_agent

    wait_agent(agent)

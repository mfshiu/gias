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
from src.blackboard.client import get_crowd_hotspots

logger = init_logging()

TOPIC = "navigation.request"


def _simulate_progress(steps: list[str], delay: float = 0.2) -> None:
    """模擬處理過程，逐步顯示進度"""
    for i, msg in enumerate(steps, 1):
        print(f"  [NavigationAgent] {i}/{len(steps)} {msg}", flush=True)
        logger.info("NavigationAgent progress: %s", msg)
        time.sleep(delay)


def _crowded_zones_from_hotspots(hotspots: list) -> list[str]:
    """從黑板查詢結果取得擁擠區列表"""
    return [h.get("zone") for h in (hotspots or []) if h.get("zone") and h.get("crowd_status") == "Crowded"]


def _execute_with_replan(
    agent: "NavigationAgent",
    task: str,
    params: dict[str, Any],
    *,
    en_route_sec: float = 5.0,
    step_delay: float = 0.8,
) -> str:
    """
    導航執行：過程中多次查詢黑板，若觀察者更新導致人潮變化則顯示【路線變更】。
    讓使用者明顯看見「因觀察者更新而改變行動方式」的過程。
    """
    loc = params.get("current_location", "?")
    target = params.get("target_name") or params.get("destination", "?")
    dest_label = params.get("destination") or target

    # 階段 1：初步規劃
    _simulate_progress([
        f"定位起點：{loc}...",
        f"搜尋目標「{target}」...",
        "【第一次查詢黑板】取得即時人潮...",
    ], delay=step_delay)
    hotspots_1 = get_crowd_hotspots(agent)
    crowded_1 = sorted(_crowded_zones_from_hotspots(hotspots_1))
    if crowded_1:
        print(f"  [NavigationAgent] >>> 初步規劃：避開擁擠區 {', '.join(crowded_1)}", flush=True)
        time.sleep(step_delay)
    else:
        print(f"  [NavigationAgent] >>> 初步規劃：各區人潮正常，採最短路徑", flush=True)
        time.sleep(step_delay)

    # 階段 2：模擬移動中（觀察者會持續更新黑板）
    print(f"  [NavigationAgent] >>> 導航進行中...（等待 {en_route_sec:.0f} 秒，觀察者持續更新人潮）", flush=True)
    time.sleep(en_route_sec)

    # 階段 3：再次查詢，檢查是否需重新規劃
    _simulate_progress([
        "【第二次查詢黑板】檢查人潮是否有變...",
    ], delay=step_delay)
    hotspots_2 = get_crowd_hotspots(agent)
    crowded_2 = sorted(_crowded_zones_from_hotspots(hotspots_2))

    # 比對兩次結果，顯示變化
    msg = f"已規劃從 {loc} 前往 {dest_label} 的路線，請跟我來。"
    if crowded_1 != crowded_2:
        print(f"  [NavigationAgent] >>> 【路線變更】人潮已有變化！", flush=True)
        if crowded_1 and not crowded_2:
            print(f"  [NavigationAgent] >>> 原擁擠區 {', '.join(crowded_1)} 現已緩解，改採最短路徑。", flush=True)
            msg += f" 【路線變更】原擁擠區 {', '.join(crowded_1)} 現已緩解，已改採最短路徑。"
        elif not crowded_1 and crowded_2:
            print(f"  [NavigationAgent] >>> 新擁擠區 {', '.join(crowded_2)}，已重新規劃繞行。", flush=True)
            msg += f" 【路線變更】{', '.join(crowded_2)} 現擁擠，已重新規劃繞行。"
        else:
            print(f"  [NavigationAgent] >>> 原 {', '.join(crowded_1)} → 現 {', '.join(crowded_2)}，已重新規劃。", flush=True)
            msg += f" 【路線變更】人潮變化：{', '.join(crowded_1)} → {', '.join(crowded_2)}，已重新規劃路線。"
        time.sleep(step_delay)
    elif crowded_2:
        msg += f" 【黑板觀察】途經區域 {', '.join(crowded_2)} 擁擠，已繞行。"
    return msg


def _execute(agent: "NavigationAgent", task: str, params: dict[str, Any]) -> str:
    """模擬執行導航類 task，含處理過程與延遲；部分 task 會查詢黑板以反映觀察者影響"""
    if task == "SuggestRoute":
        loc = params.get("current_location", "?")
        _simulate_progress([
            f"取得您的位置：{loc}...",
            "【第一次查詢黑板】取得即時人潮...",
        ], delay=0.8)
        hotspots_1 = get_crowd_hotspots(agent)
        crowded_1 = sorted(_crowded_zones_from_hotspots(hotspots_1))
        if crowded_1:
            print(f"  [NavigationAgent] >>> 初步規劃：避開 {', '.join(crowded_1)}", flush=True)
        else:
            print(f"  [NavigationAgent] >>> 初步規劃：各區人潮正常", flush=True)
        time.sleep(0.8)
        print(f"  [NavigationAgent] >>> 模擬規劃中...（5 秒，觀察者持續更新）", flush=True)
        time.sleep(5.0)
        _simulate_progress(["【第二次查詢黑板】確認人潮是否有變..."], delay=0.8)
        hotspots_2 = get_crowd_hotspots(agent)
        crowded_2 = sorted(_crowded_zones_from_hotspots(hotspots_2))
        msg = f"已根據您的位置 {loc} 規劃建議參觀路線。"
        if crowded_1 != crowded_2:
            print(f"  [NavigationAgent] >>> 【路線變更】人潮已有變化！", flush=True)
            if crowded_1 and not crowded_2:
                msg += f" 【路線變更】原擁擠區 {', '.join(crowded_1)} 現已緩解，已改採最短路徑。"
            elif not crowded_1 and crowded_2:
                msg += f" 【路線變更】{', '.join(crowded_2)} 現擁擠，已重新規劃繞行。"
            else:
                msg += f" 【路線變更】人潮變化：{', '.join(crowded_1)} → {', '.join(crowded_2)}，已重新規劃。"
        elif crowded_2:
            msg += f" 【黑板觀察】已避開擁擠區: {', '.join(crowded_2)}"
        return msg
    if task == "LocateExhibit":
        return _execute_with_replan(
            agent, task, params,
            en_route_sec=5.0,
            step_delay=0.8,
        )
    if task == "ExplainDirections":
        dest = params.get("destination", "?")
        loc = params.get("current_location", "?")
        _simulate_progress([
            f"從 {loc} 到 {dest}...",
            "向黑板查詢即時人潮...",
            "查詢展場平面圖...",
            "標示轉彎點與地標...",
        ])
        hotspots = get_crowd_hotspots(agent)
        crowded_zones = [h.get("zone") for h in (hotspots or []) if h.get("crowd_status") == "Crowded"]
        msg = f"從 {loc} 前往 {dest}：直走約 50 公尺，左轉後第二個攤位即為目標。"
        if crowded_zones:
            msg += f" 【黑板觀察】注意：{', '.join(crowded_zones)} 目前擁擠，建議避開。"
        return msg
    if task == "NavigationAssistance":
        return _execute_with_replan(
            agent, task, params,
            en_route_sec=5.0,
            step_delay=0.8,
        )
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

        message = _execute(self, task, params)

        print(f"  [NavigationAgent] ✓ 完成: {message}", flush=True)
        logger.info("NavigationAgent result: %s", str(message))
        result = build_result(task, message, action_id, intent)
        return result


if __name__ == "__main__":
    agent = NavigationAgent(agent_config=get_agent_config())
    agent.start_process()
    from src.app_helper import wait_agent

    wait_agent(agent)

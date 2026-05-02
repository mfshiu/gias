# src/run_navigation_with_observers.py
"""
情境：導航行動者 + 觀察者（可見【路線變更】過程）

專門觸發 NavigationAgent。導航過程中會「兩次查詢黑板」：
  第一次：初步規劃路線
  等待 5 秒（觀察者持續更新人潮）
  第二次：檢查人潮是否變化，若有則顯示【路線變更】並重新規劃

可明顯看見「因觀察者更新而改變行動方式」的過程。

執行：
  python -m src.run_navigation_with_observers
  python -m src.run_navigation_with_observers "從入口帶我去B_B1攤位"
  python -m src.run_navigation_with_observers "從入口規劃參觀路線"

建議意圖（會執行兩階段查詢、有機會看見【路線變更】）：
  - 從入口帶我去B_B1攤位     → LocateExhibit
  - 從入口規劃參觀路線       → SuggestRoute
  - 從入口導航到A12          → NavigationAssistance

需先啟動 MQTT broker，並執行 seed_blackboard 建立 blackboard 圖譜。
"""

from __future__ import annotations

import os
import sys
import threading
import time

from src.app_helper import get_agent_config, wait_agent
from src.blackboard.agent import BlackboardAgent
from src.observer.pedestrian_flow import PedestrianFlowObserver
from src.observer.facility_event import FacilityEventObserver
from src.agents.info_agent import InfoAgent
from src.agents.navigation_agent import NavigationAgent
from src.core.intentional_agent import IntentionalAgent
from src.run_intentional_agent import EXPO_PROFILE
from src.log_helper import init_logging

logger = init_logging()


def _run_info_agent(agent_config: dict) -> None:
    agent = InfoAgent(agent_config=agent_config)
    agent.start_thread()


def _run_navigation_agent(agent_config: dict) -> None:
    agent = NavigationAgent(agent_config=agent_config)
    agent.start_thread()


def main() -> None:
    intention = (
        (sys.argv[1] if len(sys.argv) > 1 else None)
        or os.environ.get("GIAS_INTENTION")
        or "從入口帶我去B_B1攤位"
    )
    intention = intention.strip()
    if not intention:
        logger.error("請提供意圖：python -m src.run_navigation_with_observers \"<意圖>\"")
        sys.exit(1)

    agent_config = get_agent_config()

    print("=" * 60)
    print("情境：導航行動者 + 觀察者（可見【黑板觀察】影響）")
    print("=" * 60)
    print("\n[1] 啟動 BlackboardAgent...")
    bb_agent = BlackboardAgent(agent_config=agent_config, poll_interval_sec=2.0)
    t_bb = threading.Thread(target=bb_agent.start_thread, daemon=True)
    t_bb.start()
    time.sleep(2)

    print("[2] 啟動 PedestrianFlowObserver、FacilityEventObserver...")
    print("    （人流觀察者每 1 秒更新，導航過程中會多次查詢黑板以偵測變化）")
    pf_observer = PedestrianFlowObserver(
        agent_config=agent_config,
        poll_interval_sec=1.0,
    )
    fe_observer = FacilityEventObserver(
        agent_config=agent_config,
        poll_interval_sec=3.0,
    )
    t_pf = threading.Thread(target=pf_observer.start_thread, daemon=True)
    t_fe = threading.Thread(target=fe_observer.start_thread, daemon=True)
    t_pf.start()
    t_fe.start()
    time.sleep(2)

    print("[3] 啟動 InfoAgent、NavigationAgent...")
    t_info = threading.Thread(target=_run_info_agent, args=(agent_config,), daemon=True)
    t_nav = threading.Thread(target=_run_navigation_agent, args=(agent_config,), daemon=True)
    t_info.start()
    t_nav.start()
    time.sleep(3)

    print("[4] 等待觀察者更新黑板（約 6 秒，讓導航過程中有足夠時間偵測人潮變化）...")
    time.sleep(6)

    print("\n" + "=" * 60)
    print(f"IntentionalAgent 意圖: {intention}")
    print("（導航行動者將查詢黑板，顯示【黑板觀察】訊息）")
    print("=" * 60 + "\n")

    agent = IntentionalAgent(
        agent_config=agent_config,
        intention=intention,
        domain_profile=EXPO_PROFILE,
    )
    agent.start_thread()
    wait_agent(agent)


if __name__ == "__main__":
    main()

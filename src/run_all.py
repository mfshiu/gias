# src/run_all.py
"""
單一終端機啟動所有 Agent（InfoAgent、NavigationAgent、IntentionalAgent），
讓 log 與處理過程集中顯示於同一視窗。

執行：
  python -m src.run_all "我在入口，想看 AI 展區，請推薦幾個不擁擠的展區並幫我規劃順路的參觀路線。"

需先啟動 MQTT broker。
"""

from __future__ import annotations

import os
import sys
import threading
import time

from src.app_helper import get_agent_config
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
        or "帶我去A12攤位，並介紹智慧導覽眼鏡"
    )
    intention = intention.strip()
    if not intention:
        logger.error("請提供意圖：python -m src.run_all \"<意圖>\"")
        sys.exit(1)

    agent_config = get_agent_config()

    print("=" * 60)
    print("啟動 InfoAgent、NavigationAgent（thread 模式）...")
    print("=" * 60)

    t_info = threading.Thread(target=_run_info_agent, args=(agent_config,), daemon=True)
    t_nav = threading.Thread(target=_run_navigation_agent, args=(agent_config,), daemon=True)
    t_info.start()
    t_nav.start()

    # 等待 broker 連線與 subscribe 完成
    time.sleep(3)

    print("\n" + "=" * 60)
    print(f"IntentionalAgent 意圖: {intention}")
    print("=" * 60 + "\n")

    agent = IntentionalAgent(
        agent_config=agent_config,
        intention=intention,
        domain_profile=EXPO_PROFILE,
    )
    agent.start_thread()
    from src.app_helper import wait_agent

    wait_agent(agent)


if __name__ == "__main__":
    main()

# src/run_actors.py
"""
只啟動行動者（InfoAgent、NavigationAgent，可選 IntentionalAgent）

執行：
  python -m src.run_actors
  python -m src.run_actors "帶我去洗手間"

不提供意圖時，僅啟動 InfoAgent 與 NavigationAgent，持續等待 MQTT 請求。
提供意圖時，額外啟動 IntentionalAgent 並執行規劃與 publish。

需先啟動 MQTT broker。
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time

from src.app_helper import get_agent_config, wait_agent
from src.agents.info_agent import InfoAgent
from src.agents.navigation_agent import NavigationAgent
from src.core.intentional_agent import IntentionalAgent
from src.run_intentional_agent import EXPO_PROFILE
from src.log_helper import init_logging

logger = init_logging()

_shutdown = False


def _signal_handler(signum, frame):
    global _shutdown
    _shutdown = True


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
        or None
    )
    if intention is not None:
        intention = intention.strip() or None

    agent_config = get_agent_config()

    print("=" * 60)
    print("啟動行動者：InfoAgent、NavigationAgent")
    print("=" * 60)

    t_info = threading.Thread(target=_run_info_agent, args=(agent_config,), daemon=True)
    t_nav = threading.Thread(target=_run_navigation_agent, args=(agent_config,), daemon=True)
    t_info.start()
    t_nav.start()

    time.sleep(3)

    if intention:
        print("\n" + "=" * 60)
        print(f"IntentionalAgent 意圖: {intention}")
        print("=" * 60 + "\n")

        agent = IntentionalAgent(
            agent_config=agent_config,
            intention=intention,
            domain_profile=EXPO_PROFILE,
        )
        agent.start_thread()
        wait_agent(agent)
    else:
        signal.signal(signal.SIGINT, _signal_handler)
        print("\n行動者已啟動，等待 MQTT 請求。按 Ctrl+C 結束。\n")
        while not _shutdown:
            time.sleep(1)


if __name__ == "__main__":
    main()

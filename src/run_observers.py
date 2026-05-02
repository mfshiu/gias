# src/run_observers.py
"""
啟動展場觀察者代理與黑板代理

包含：
- BlackboardAgent：黑板代理，接收觀察並更新 KG
- PedestrianFlowObserver：人流與動線狀態代理
- FacilityEventObserver：設施與活動狀態代理

執行：python -m src.run_observers

需先啟動 MQTT broker，並執行 seed_blackboard 建立 blackboard 圖譜。
"""

from __future__ import annotations

import threading
import time

from src.app_helper import get_agent_config, wait_agent
from src.blackboard.agent import BlackboardAgent
from src.observer.pedestrian_flow import PedestrianFlowObserver
from src.observer.facility_event import FacilityEventObserver
from src.log_helper import init_logging

logger = init_logging()


def main() -> None:
    agent_config = get_agent_config()

    print("=" * 60)
    print("啟動 BlackboardAgent、PedestrianFlowObserver、FacilityEventObserver")
    print("=" * 60)

    # 黑板代理（需先啟動以接收觀察）
    bb_agent = BlackboardAgent(agent_config=agent_config, poll_interval_sec=2.0)
    t_bb = threading.Thread(target=bb_agent.start_thread, daemon=True)
    t_bb.start()

    time.sleep(2)

    # 人流與動線觀察者
    pf_observer = PedestrianFlowObserver(
        agent_config=agent_config,
        poll_interval_sec=2.0,
    )
    t_pf = threading.Thread(target=pf_observer.start_thread, daemon=True)
    t_pf.start()

    # 設施與活動觀察者
    fe_observer = FacilityEventObserver(
        agent_config=agent_config,
        poll_interval_sec=3.0,
    )
    t_fe = threading.Thread(target=fe_observer.start_thread, daemon=True)
    t_fe.start()

    print("\n觀察者代理已啟動，按 Ctrl+C 結束。\n")

    # 以 BlackboardAgent 為主等待（其為核心）
    wait_agent(bb_agent)


if __name__ == "__main__":
    main()

"""
啟動 Navigation Scenario 感測器代理 + BlackboardAgent

感測器會依各自特性，以隨機機率向 BlackboardAgent 發送 observe / write，
更新 Blackboard KG（人潮、展位、通道封鎖、場館 API 公告）。

執行：
  python -m navigation_scenario.run_sensors

前置：
  - MQTT broker 已啟動（gias.toml [broker]）
  - 已執行 navigation_scenario.seed_blackboard
"""

from __future__ import annotations

import argparse
import os
import random
import threading
import time


def _start_agent_thread(agent) -> threading.Thread:
    t = threading.Thread(target=agent.start_thread, daemon=True)
    t.start()
    return t


def build_sensors(
    agent_config: dict,
    *,
    seed: int | None = None,
) -> list:
    # 延遲 import：避免在 LOG_LEVEL 被設定前載入 log_helper
    from navigation_scenario.sensors import (
        PedestrianFlowSensorAgent,
        FacilityEventSensorAgent,
        VisualSensorAgent,
        DigitalSensorAgent,
    )

    rng = random.Random(seed) if seed is not None else random.Random()

    return [
        PedestrianFlowSensorAgent(agent_config=agent_config, rng=rng),
        FacilityEventSensorAgent(agent_config=agent_config, rng=random.Random(rng.randint(0, 2**31))),
        VisualSensorAgent(agent_config=agent_config, rng=random.Random(rng.randint(0, 2**31))),
        DigitalSensorAgent(agent_config=agent_config, rng=random.Random(rng.randint(0, 2**31))),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="啟動 Navigation Scenario 感測器 + Blackboard")
    parser.add_argument("--seed", type=int, default=42, help="隨機種子（可重現）")
    parser.add_argument(
        "--bb-poll",
        type=float,
        default=2.0,
        help="BlackboardAgent watcher 輪詢間隔（秒）",
    )
    parser.add_argument(
        "--log-level",
        choices=["VERBOSE", "DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help=(
            "覆寫 LOG_LEVEL 環境變數。"
            "VERBOSE 會額外輸出每個感測器的 Cypher 與參數；"
            "INFO 只顯示中文白話的感測行為描述。"
        ),
    )
    args = parser.parse_args()

    if args.log_level:
        os.environ["LOG_LEVEL"] = args.log_level

    # 必須在設定 LOG_LEVEL 之後再 import / 初始化 logger
    from src.app_helper import get_agent_config, wait_agent
    from src.blackboard.agent import BlackboardAgent
    from src.log_helper import init_logging

    from navigation_scenario.sensors import (
        PedestrianFlowSensorAgent,
        FacilityEventSensorAgent,
        VisualSensorAgent,
        DigitalSensorAgent,
    )

    logger = init_logging()

    agent_config = get_agent_config()

    print("\n=== navigation_scenario.run_sensors ===")
    print(f"  seed={args.seed}")

    print("\n[1] 啟動 BlackboardAgent...")
    bb_agent = BlackboardAgent(
        agent_config=agent_config,
        poll_interval_sec=args.bb_poll,
    )
    _start_agent_thread(bb_agent)
    time.sleep(2)

    print("[2] 啟動感測器代理（人流 / 設施 / 視覺 / 數位）...")
    sensors = build_sensors(agent_config, seed=args.seed)
    for s in sensors:
        _start_agent_thread(s)
        print(
            f"    - {s.sensor_kind():16s} id={s.sensor_id} "
            f"p_contact={s.contact_probability:.2f} interval={s.poll_interval_sec:.1f}s"
        )
    time.sleep(1)

    print("\n感測器運行中（隨機聯絡 Blackboard 更新 KG）。按 Ctrl+C 結束。\n")
    try:
        wait_agent(bb_agent)
    except KeyboardInterrupt:
        print("\n結束。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

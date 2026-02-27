# src/run_intentional_agent.py
"""
IntentionalAgent 執行腳本：規劃意圖並透過 broker 實際 publish 至 InfoAgent / NavigationAgent。

執行：
  python -m src.run_intentional_agent "我在入口附近，想前往A12攤位"
  python -m src.run_intentional_agent "介紹智慧導覽眼鏡，然後帶我去"
  set GIAS_INTENTION=帶我去洗手間 & python -m src.run_intentional_agent

需先啟動 MQTT broker，且建議同時啟動 InfoAgent 與 NavigationAgent 以接收 publish。
若要在同一終端機看到所有 Agent 的 log，請使用：python -m src.run_all "<意圖>"
"""

from __future__ import annotations

import os
import sys

from src.app_helper import get_agent_config, wait_agent
from src.core.intentional_agent import IntentionalAgent
from src.core.intent.domain_profile import DomainProfile
from src.log_helper import init_logging

logger = init_logging()

# 展場領域 profile（與 seed_actions 對應，利於 action match）
EXPO_PROFILE = DomainProfile(
    name="expo",
    synonym_rules=[
        (r"廠商", "展商"),
        (r"有賣", "販售"),
        (r"賣", "販售"),
        (r"帶我去", "引導我前往"),
        (r"去", "前往"),
    ],
    action_alias={
        "RecommendExhibits": ["推薦", "哪裡有", "有賣", "販售", "找", "展商", "攤位", "廠商"],
        "LocateExhibit": ["帶我去", "引導我前往", "前往", "在哪", "位置"],
        "ExplainExhibit": ["介紹", "說明", "展品"],
        "ExplainDirections": ["怎麼走", "怎麼去", "路線", "方向"],
        "SuggestRoute": ["路線", "怎麼安排", "規劃"],
        "CrowdStatus": ["人多", "擁擠", "人潮"],
        "LocateFacility": ["洗手間", "廁所", "服務台", "無障礙"],
        "AnswerFAQ": ["幾點", "開放", "閉館", "FAQ"],
        "ProvideSchedule": ["活動", "時間", "schedule"],
        "NavigationAssistance": ["導航", "帶路"],
    },
    slot_map={
        "target_name": ["destination", "target", "目標", "target_name", "終點"],
        "target_type": ["類型", "目標類型", "type"],
        "current_location": ["location", "目前位置", "起點", "current_location", "出發點"],
        "destination": ["target", "目標", "target_name", "終點"],
    },
    enum_alias={
        "target_type": {"攤位": "booth", "展區": "exhibit_zone", "展品": "exhibit"},
        "facility_type": {"廁所": "restroom", "洗手間": "restroom", "出口": "exit", "服務台": "service_desk", "無障礙": "accessible"},
    },
)


def main() -> None:
    intention = (
        (sys.argv[1] if len(sys.argv) > 1 else None)
        or os.environ.get("GIAS_INTENTION")
        or "帶我去A12攤位，並介紹智慧導覽眼鏡"
    )
    intention = intention.strip()
    if not intention:
        logger.error("請提供意圖：python -m src.run_intentional_agent \"<意圖>\"")
        sys.exit(1)

    logger.info("IntentionalAgent 意圖: %s", intention)

    agent_config = get_agent_config()
    agent = IntentionalAgent(
        agent_config=agent_config,
        intention=intention,
        domain_profile=EXPO_PROFILE,
    )
    # 使用 start_thread 避免 pickle：IntentionalAgent 含 LLMClient、Neo4j 等不可序列化物件
    agent.start_thread()
    wait_agent(agent)


if __name__ == "__main__":
    main()

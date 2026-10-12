# src/agents/__init__.py
"""
GIAS Agent 實作

- InfoAgent：提供資訊（ExplainExhibit, AnswerFAQ, LocateFacility, ProvideSchedule, RecommendExhibits, CrowdStatus）
- NavigationAgent：帶領/導航（LocateExhibit, SuggestRoute, ExplainDirections, NavigationAssistance）

兩者都是示範用執行端：任務以固定訊息模擬，結果標 simulated=True（見 docs/monitoring_protocol.md §3）。
"""

from .info_agent import InfoAgent
from .navigation_agent import NavigationAgent

__all__ = ["InfoAgent", "NavigationAgent"]

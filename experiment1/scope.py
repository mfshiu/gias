"""
Experiment 1 的驗證範圍

experiment1 只驗證 GIAS 的「規劃」：每個案例呼叫一次 IntentionalAgent.plan_intention()。
執行與重規劃都是圖論模擬（Dijkstra）：路徑依測試案例的 goal_zones 計算，不讀計畫內容；
中途事件使路徑失效時再跑一次 Dijkstra，不經過 IntentionalAgent 的監測迴圈或重規劃。
GIAS 產生的計畫只決定 PE（PE 不通過的案例記為失敗）。

執行與調適的端到端驗證由 navigation_scenario 的 live 模式取代。
結果 JSON 與 summary 以 MODE 標示這個範圍；既有的結果檔沒有 mode 欄位，範圍相同。
"""

from __future__ import annotations


MODE = "planning_only"

# 各階段由誰執行：gias = 呼叫 GIAS；simulation = Dijkstra 模擬
VALIDATION_SCOPE = {
    "planning": "gias",
    "execution": "simulation",
    "replanning": "simulation",
}

# 各指標由什麼決定（與 analyze.HEADER 的指標欄位對應）
METRIC_SOURCES = {
    "TSR": "simulation",
    "PE": "gias",
    "ISR": "simulation",
    "RSR": "simulation",
    "constraint_rate": "simulation",
    "avg_path_m": "simulation",
    "avg_time_s": "simulation",
}

SOURCE_LABELS = {
    "gias": "GIAS 計畫",
    "simulation": "Dijkstra 模擬",
}

SUPERSEDED_BY = {
    "scenario": "navigation_scenario",
    "mode": "live",
    "command": "python -X utf8 -m navigation_scenario.batch_runner --live",
}

NOTICE_LINES = (
    "驗證範圍：只有規劃經過 GIAS（IntentionalAgent.plan_intention）；執行與重規劃是 Dijkstra 模擬，"
    "路徑依測試案例的 goal_zones 計算，與計畫內容無關（PE 不通過的案例記為失敗）。",
    "執行與調適的端到端驗證請改用 navigation_scenario 的 live 模式：" + SUPERSEDED_BY["command"],
)


def scope_payload() -> dict:
    """寫入結果 JSON 的範圍欄位。"""
    return {
        "mode": MODE,
        "validation_scope": dict(VALIDATION_SCOPE),
        "superseded_by": dict(SUPERSEDED_BY),
    }

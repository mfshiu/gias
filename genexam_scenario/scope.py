"""
GenExam Scenario 的驗證範圍

runner 依固定順序執行「建立 session → 檢索 → 生成 → 驗證 → 修正 → 寫回」，只用到 GIAS 的
LLMClient 與 Blackboard KG（Neo4jBoltAdapter）；沒有經過 IntentionalAgent 的規劃、Action KG 比對、
監測迴圈（retry／replan）或 MQTT 執行端。seed_actions 寫入的 11 個 GVR action 目前沒有程式使用。
因此結果反映的是這條出題管線與 LLM 的表現，不能歸功於 GIAS 的意圖規劃與調適。

另外，CSR 與 SC 只計算通過驗證的題目，並沿用驗證時的同一套規則（metrics._check_constraints、
_is_schema_consistent），所以只要有題目通過，SC 就是 100%；未使用 --llm-judge 時 CSR 也是 100%
（驗證比對的是 LLM 自己標示的難度／Bloom／題型），Task Success 實際上等於題數是否達標。

結果 JSON、manifest 與 report.md 以 SCOPE 標示這個範圍；既有的結果檔沒有這個欄位，範圍相同。
（manifest 原有的 mode 欄位是 dry-run／live，指是否呼叫 LLM，與這裡的範圍無關。）
"""

from __future__ import annotations


SCOPE = "standalone_pipeline"

# 各階段由誰執行：fixed_pipeline = runner 的固定流程（直接呼叫 LLMClient 與 Cypher）
VALIDATION_SCOPE = {
    "planning": "fixed_pipeline",     # 固定順序；PlanGeneration 只是寫入 GenerationSession 節點
    "execution": "fixed_pipeline",    # runner 直接呼叫 LLMClient 與 Blackboard，不經 MQTT 執行端
    "replanning": "fixed_pipeline",   # 驗證不通過時由 runner 的迴圈改寫（最多 max_refine 次），不經監測迴圈
}

GIAS_COMPONENTS = {
    "used": ["LLMClient", "Blackboard KG (Neo4jBoltAdapter)"],
    "not_used": [
        "IntentionalAgent planning",
        "Action KG matching",
        "monitoring loop (retry / replan)",
        "MQTT executor agents",
    ],
}

# 各指標實際量到什麼（與 report.md 主表的指標對應）
METRIC_NOTES = {
    "KC": "通過驗證的題目引用到的概念數 ÷ 該主題的概念數",
    "CSR": "以驗證時的同一套規則重新檢查通過驗證的題目；未使用 --llm-judge 時只要有題目通過就是 100%",
    "SC": "以驗證時的同一套 schema 規則重新檢查通過驗證的題目；只要有題目通過就是 100%",
    "Task Success Rate": "題數達標且 CSR、SC 達門檻；未使用 --llm-judge 時等於題數是否達標",
    "Gen Time": "整條管線（含 LLM 呼叫）的 wall-clock 秒數",
}

NOTICE_LINES = (
    "驗證範圍：GenExam 是獨立的出題管線，只用到 GIAS 的 LLMClient 與 Blackboard KG；"
    "規劃、執行與修正都是 runner 的固定流程，沒有經過 IntentionalAgent 的規劃與監測（retry／replan）。",
    "CSR 與 SC 只計算通過驗證的題目並沿用驗證時的規則，只要有題目通過就是 100%"
    "（CSR 在使用 --llm-judge 時例外）；Task Success 實際上取決於題數是否達標。",
)


def scope_payload() -> dict:
    """寫入結果 JSON 與 manifest 的範圍欄位。"""
    return {
        "scope": SCOPE,
        "validation_scope": dict(VALIDATION_SCOPE),
        "gias_components": {k: list(v) for k, v in GIAS_COMPONENTS.items()},
    }

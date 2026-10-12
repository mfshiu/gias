"""
GenExam Scenario 的驗證範圍

runner 依固定順序執行「建立 session → 檢索 → 生成 → 驗證 → 修正 → 寫回」，只用到 GIAS 的
LLMClient 與 Blackboard KG（Neo4jBoltAdapter）；沒有經過 IntentionalAgent 的規劃、Action KG 比對、
監測迴圈（retry／replan）或 MQTT 執行端。seed_actions 寫入的 11 個 GVR action 目前沒有程式使用。
因此結果反映的是這條出題管線與 LLM 的表現，不能歸功於 GIAS 的意圖規劃與調適。

CSR 與 SC 以要求的題數為分母，每個題位取最後一次的題目（未通過驗證、未產出都算不符合）。CSR 由生成
結束後另外呼叫的 LLM 評審判定難度／Bloom／題型，不比對 LLM 自己標示的值；使用 --llm-judge 時沿用驗證時的
judge（不是獨立判斷），dry-run 或 --no-eval-judge 時為 N/A。判定來源記在 CaseMetrics.csr_source。
舊版結果（csr_source = legacy）只看通過驗證的題目並比對自我標示，CSR、SC 必為 100%（V-07）。

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
    "CSR": "每個題位最後一次的題目中，評審判定符合所有限制的比例（分母為要求的題數）；"
           "判定來源見「CSR 判定來源」，N/A 表示無法評審",
    "SC": "每個題位最後一次的題目中，schema 一致的比例（分母為要求的題數，未通過驗證、未產出都算不一致）",
    "Task Success Rate": "通過驗證的題數達標，且 CSR、SC 達門檻（CSR 為 N/A 時不檢查 CSR）",
    "Gen Time": "整條管線（含 LLM 呼叫）的 wall-clock 秒數，不含 CSR 評審",
}

NOTICE_LINES = (
    "驗證範圍：GenExam 是獨立的出題管線，只用到 GIAS 的 LLMClient 與 Blackboard KG；"
    "規劃、執行與修正都是 runner 的固定流程，沒有經過 IntentionalAgent 的規劃與監測（retry／replan）。",
    "CSR 與 SC 以要求的題數為分母；CSR 由生成結束後的 LLM 評審判定，不比對 LLM 自己標示的值"
    "（--llm-judge 時沿用驗證的 judge，不是獨立判斷；dry-run 或 --no-eval-judge 時為 N/A）。",
)


def scope_payload() -> dict:
    """寫入結果 JSON 與 manifest 的範圍欄位。"""
    return {
        "scope": SCOPE,
        "validation_scope": dict(VALIDATION_SCOPE),
        "gias_components": {k: list(v) for k, v in GIAS_COMPONENTS.items()},
    }

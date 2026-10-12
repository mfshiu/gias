"""
GenExam Scenario：試題生成（評估二）測試套件

以 GVR（Generator → Verifier → Refiner）閉環執行「限制驅動」的試題生成，
評估限制條件的滿足與 Knowledge Grounding。

測試類型（共 60 案，限制條件在生成前固定，不做中途注入）：
  - single_constraint  : 單一約束（30 例，難度 OR 題型）
  - multi_constraint   : 多重約束（30 例，難度 + Bloom + 題型）

驗證範圍：runner 是獨立的出題管線，只用到 GIAS 的 LLMClient 與 Blackboard KG，
沒有經過 IntentionalAgent 的規劃與監測（見 scope.py）。

模組結構：
  config.py          # Topic / Subtopic / Concept / Fact / Source 與常數
  seed_actions.py    # 寫入 Action KG（neo4j 'actions' database）11 個 GVR actions
  seed_blackboard.py # 寫入 Blackboard KG（neo4j 'blackboard' database）
                     # 含「領域知識子圖（KG-RAG 來源）」與「生成狀態子圖（GVR 工作空間）」
  test_cases.py      # 60 個固定限制的測試案例（30 single + 30 multi）
  metrics.py         # 5 項指標：KC / CSR / SC / Task Success Rate / Gen Time
  runner.py          # 單案例執行器（GVR 閉環，真實 LLM；不經過 IntentionalAgent）
  scope.py           # 驗證範圍（scope = standalone_pipeline）與各指標實際量到的內容
  batch_runner.py    # 批次執行 60 案例 → genexam_scenario_results/run_<ts>/
  analyze.py         # 彙整 CSV + Markdown 報告

執行流程：
  # 1. 建立 KG（每次切換 scenario 需重新 seed）
  python -m genexam_scenario.seed_actions
  python -m genexam_scenario.seed_blackboard

  # 2. 確認 60 案測試集
  python -m genexam_scenario.test_cases

  # 3. 確認指標公式（不需 Neo4j / LLM）
  python -m genexam_scenario.metrics

  # 4. 執行單一案例（除錯用）
  python -m genexam_scenario.runner single_diff_02 --dry-run
  python -m genexam_scenario.runner single_diff_02

  # 5. 批次執行 + 自動 analyze
  python -m genexam_scenario.batch_runner --dry-run --limit 4   # 快速 smoke
  python -m genexam_scenario.batch_runner                       # 完整 60 案
  python -m genexam_scenario.batch_runner --llm-judge           # 嚴格驗證

  # 6. 重新分析既有結果
  python -m genexam_scenario.analyze genexam_scenario_results/run_xxxxx
"""

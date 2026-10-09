"""
GenExam Scenario：試題生成（評估二）測試套件

驗證 GIAS 在「限制驅動」的試題生成任務下，是否能透過 GVR
（Generator → Verifier → Refiner）閉環，
維持 Intention 穩定、實現 Knowledge Grounding、並協作完成生成。

測試類型（共 60 案）：
  - single_constraint  : 單一動態約束（30 例，難度 OR 題型）
  - multi_constraint   : 多重動態約束（30 例，難度 + Bloom + 題型）

在生成進度 Initial（起始）或 Mid-generation（中段）時，
注入新增 / 變更 / 收緊 / 鬆綁的約束條件，
觀察是否能正確 Refine 並維持原 Intention。

模組結構：
  config.py          # Topic / Subtopic / Concept / Fact / Source 與常數
  seed_actions.py    # 寫入 Action KG（neo4j 'actions' database）11 個 GVR actions
  seed_blackboard.py # 寫入 Blackboard KG（neo4j 'blackboard' database）
                     # 含「領域知識子圖（KG-RAG 來源）」與「生成狀態子圖（GVR 工作空間）」
  test_cases.py      # 60 個固定限制的測試案例（30 single + 30 multi）
  metrics.py         # 5 項指標：KC / CSR / SC / Task Success Rate / Gen Time
  runner.py          # 單案例執行器（GVR 閉環，真實 LLM）
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

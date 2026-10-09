"""
Navigation Scenario live 模式：以真實 GIAS 流程（LLM 規劃 → 監測迴圈 → MQTT 派工 →
在 Blackboard 圖上實際移動 → 事件觸發重規劃）執行導航案例，作為端到端 benchmark。

模組：
  guide_agent.py  # GuideAgent：在 Blackboard 圖上實際移動的嚮導機器人（navigation executor）
  environment.py  # 事件注入（直接寫 Blackboard / 經感測器）、機器人位置寫入
  evaluation.py   # 從機器人實際軌跡計算 CaseMetrics
  runner.py       # 單一案例執行、LiveSession（啟動真實 agent）、CLI
"""

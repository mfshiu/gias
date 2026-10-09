"""
Navigation Scenario：動態環境下的導航能力測試套件

本子專案為 GIAS 的 Navigation Scenario 測試案例，包含三組共 60 個案例：
  - single_target     : 單目標導航（20 例）
  - constrained       : 限制式導航（20 例，含避開擁擠 / 封閉區域 / 改道）
  - multi_step        : 多步驟導航（20 例，依序前往多個目標）

於任務進度 30% 或 60% 時注入動態事件（人潮壅塞 / 區域關閉 / 改道），
收集 完成時間、路徑效率、是否成功重規劃 等指標，
驗證 GIAS 在動態環境下的穩定性與適應能力。

模組結構：
  config.py          # 展場地圖（zones / pois / booths / edges）與常數
  seed_actions.py    # 寫入 Action KG（neo4j 'actions' database）
  seed_blackboard.py # 寫入 Blackboard KG（neo4j 'blackboard' database）

執行流程：
  # 1. 建立 KG
  python -m navigation_scenario.seed_actions
  python -m navigation_scenario.seed_blackboard

  # 2. 啟動感測器（接收 MQTT 注入指令，更新 Blackboard KG）
  python -m navigation_scenario.run_sensors

  # 3. 跑單一案例
  python -m navigation_scenario.runner single_01_30

  # 4. 跑完 60 個案例 + 自動分析
  python -m navigation_scenario.batch_runner
"""

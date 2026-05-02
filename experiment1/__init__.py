"""
Experiment 1：展場導航測試（5.3.5 Test Cases）

三組測試（每組 20 案例，共 60 案例），於兩種事件注入比例（30%/60%）下執行：
  - Single-Goal Navigation：純目標導向 → TSR, PE
  - Constraint-Based Navigation：含限制（避開擁擠/繞路）→ TSR, ISR, RSR
  - Multi-Step Navigation：多步驟順序導航 → ISR, RSR, PE

整體流程：
  seed_actions → seed_blackboard → run_<group> → analyze
"""

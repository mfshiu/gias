# Experiment 1 Summary

**Mode**：`planning_only`

- 驗證範圍：只有規劃經過 GIAS（IntentionalAgent.plan_intention）；執行與重規劃是 Dijkstra 模擬，路徑依測試案例的 goal_zones 計算，與計畫內容無關（PE 不通過的案例記為失敗）。
- 執行與調適的端到端驗證請改用 navigation_scenario 的 live 模式：python -X utf8 -m navigation_scenario.batch_runner --live

Sources: 3 file(s), 120 case(s) in total.

## Aggregated Metrics by (group, ratio)

| group | ratio | n | TSR | PE | ISR | RSR | constraint_rate | avg_path_m | avg_time_s |
|---|---|---|---|---|---|---|---|---|---|
| constraint_based | 0.3 | 20 | 1.0 | 1.0 | 1.0 | N/A | 1.0 | 72.0 | 72.0 |
| constraint_based | 0.6 | 20 | 0.9 | 0.95 | 0.9 | 0.5 | 1.0 | 62.0 | 62.25 |
| multi_step | 0.3 | 20 | 0.85 | 0.9 | 0.85 | 1.0 | 1.0 | 121.5 | 121.75 |
| multi_step | 0.6 | 20 | 0.75 | 0.9 | 0.75 | 0.6667 | 1.0 | 113.0 | 113.5 |
| single_goal | 0.3 | 20 | 0.95 | 0.95 | 0.95 | N/A | 1.0 | 67.5 | 67.5 |
| single_goal | 0.6 | 20 | 1.0 | 1.0 | 1.0 | N/A | 1.0 | 71.0 | 71.0 |

## Notes

- TSR : Task Success Rate（來源：Dijkstra 模擬）
- PE : Plan Executability（來源：GIAS 計畫）
- ISR : Intention Stability Rate（來源：Dijkstra 模擬）
- RSR : Replanning Success Rate (N/A if no replan triggered)（來源：Dijkstra 模擬）
- constraint_rate : 受限案例中限制條件被滿足的比例（來源：Dijkstra 模擬）
- avg_path_m : 平均路徑長度（公尺）（來源：Dijkstra 模擬）
- avg_time_s : 平均完成時間（秒，含 replan 懲罰）（來源：Dijkstra 模擬）

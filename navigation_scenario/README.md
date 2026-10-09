# Navigation Scenario：動態環境導航測試

驗證 GIAS 在動態環境（人潮壅塞、區域關閉、改道）下的**穩定性**與**適應能力**。

> **完整執行手冊**：[`docs/navigation_scenario_runbook.md`](../docs/navigation_scenario_runbook.md)

## 測試規劃

| 組別 | 案例數 | 範例 | 主要指標 |
|---|---|---|---|
| **single_target**（單目標導航） | 20 | 「請帶我去 AI 展區」 | 完成時間、路徑效率 |
| **constrained**（限制式導航） | 20 | 「請帶我去 AI 展區，避開擁擠區域」 | 完成時間、路徑效率、replan 成功率 |
| **multi_step**（多步驟導航） | 20 | 「先去機器人展區，再去 AI 展區」 | 完成時間、路徑效率、replan 成功率 |

動態事件 `crowd_congestion` / `area_closure` / `route_detour` 於任務進度
**30%** 或 **60%** 時注入。

## 模組結構

```
navigation_scenario/
  __init__.py
  config.py          # 展場地圖（10 zones, 9 POIs, 11 booths, 25 雙向邊）、Domain Profile、事件常數
  seed_actions.py    # 寫入 Action KG（[kg.neo4j_actions] = 'actions' database）
  seed_blackboard.py # 寫入 Blackboard KG（[kg.neo4j_blackboard] = 'blackboard' database）
  README.md
```

## 感測器代理（Sensor Agents）

四類感測器皆繼承 **AgentFlow `Agent`**，週期輪詢並以 `contact_probability` **隨機**向
`BlackboardAgent`（`blackboard.control`）發送 `observe` 或 `write`，更新 Blackboard KG：

| 感測器 | 模組 | 聯絡機率 | 輪詢 | 更新方式（皆為 `write`，**不建 Observation**） |
|--------|------|----------|------|------------------------------------------------|
| 人流／動線 | `PedestrianFlowSensorAgent` | ~0.55 | ~2s | UNWIND 更新 Zone `CURRENT_STATE` + 屬性 |
| 設施／展位 | `FacilityEventSensorAgent` | ~0.40 | ~3s | UNWIND 更新 Booth.status / POI.facility_status |
| 視覺／通道 | `VisualSensorAgent` | ~0.28 | ~1.8s | SET `CONNECTED_TO.blocked`（**僅狀態變化時**才送） |
| 數位／API | `DigitalSensorAgent` | ~0.38 | ~4s | 重建 Zone `CURRENT_STATE`（同狀態則機率跳過） |

> 所有感測器都僅 **MATCH + SET / MERGE 既有節點**，不送 `observe`，
> 因此長時間運行也不會在 Blackboard 累積 `Observation` 歷史節點。

```powershell
# 需 MQTT broker + 已 seed blackboard
python -X utf8 -m navigation_scenario.run_sensors
python -X utf8 -m navigation_scenario.run_sensors --seed 42

# 顯示詳細 Cypher（VERBOSE）
python -X utf8 -m navigation_scenario.run_sensors --log-level VERBOSE
# 只看白話行為描述（INFO）
python -X utf8 -m navigation_scenario.run_sensors --log-level INFO
```

### 日誌等級

| 等級 | 內容 |
|------|------|
| `INFO` | 中文白話的感測行為描述（例：「視覺感測：偵測到通道 P_South_Hub → B_GM1 出現人潮阻擋，將該邊（雙向）標記為封鎖。」） |
| `VERBOSE` | 額外輸出實際 Cypher 與參數（長 `rows` 會自動截斷至前 3 筆） |

### 動態調整介面（MQTT 訊息）

感測器啟動後會訂閱 3 層控制 topic，**等待其他 Agent**（如後續會建立的
`event_injector`）發訊息來變更頻率、隨機數值，或注入動態事件：

| 層級 | Topic |
|------|-------|
| 單一實例 | `navigation_scenario.sensor.<sensor_id>.control` |
| 同類廣播 | `navigation_scenario.sensors.<sensor_kind>.control`（`pedestrian_flow` / `facility_event` / `visual` / `digital`） |
| 全體廣播 | `navigation_scenario.sensors.control` |

支援的指令：`status` / `set_contact_probability` / `set_interval` /
`set_random_param` / `inject_event` / `pause` / `resume` /
`clear_overrides` / `sample_now`。

`inject_event` 可注入：`crowd_congestion` / `crowd_clear` / `crowd_sparse` /
`route_detour` / `route_clear` / `area_closure` / `area_reopen` /
`crowd_alert` / `route_advisory` / `booth_closure` / `booth_reopen` /
`facility_outage` / `facility_restore`。

完整 payload 格式、`set_random_param` 可用參數、實際 publish 範例請見
[`docs/navigation_scenario_runbook.md § 5`](../docs/navigation_scenario_runbook.md#5-感測器控制協定mqtt)。

## 測試案例與評估

| 模組 | 角色 |
|------|------|
| `test_cases.py` | 60 個案例定義（20×3 類別，含 30%/60% 注入） |
| `pathing.py` | 從 Blackboard KG 抓圖、Dijkstra 路徑規劃（支援 avoid_crowded / avoid_closed / avoid_blocked） |
| `event_injector.py` | paho-mqtt 注入器，向感測器發送 `inject_event` |
| `metrics.py` | 定義 **TSR / ISR / RSR / PE** 指標 |
| `runner.py` | 單案例執行器：逐步走路徑、在 30%/60% 注入、replan、收集指標 |
| `batch_runner.py` | 批次執行 60 案例，輸出 `navigation_scenario_results/run_<ts>/*.json` |
| `analyze.py` | 彙整批次結果為 `summary.csv` / `aggregate.csv` / `report.md` |

### 執行

```powershell
# 跑單一案例（需 Neo4j + MQTT broker + 感測器已啟動）
python -X utf8 -m navigation_scenario.runner single_01_30

# 跑完整 60 個案例 + 自動分析
python -X utf8 -m navigation_scenario.batch_runner

# 只跑某個類別 / 某個注入時機
python -X utf8 -m navigation_scenario.batch_runner --category constrained --pct 60

# 已有結果只重新分析
python -X utf8 -m navigation_scenario.analyze navigation_scenario_results/run_20260524_193000
```

### live 模式（真實 GIAS 流程）

上面的 `runner` / `batch_runner` 是**圖論模擬**，不會呼叫 IntentionalAgent。加上 `--live` 會改用
`navigation_scenario.live`：真實 LLM 規劃 → 監測迴圈經 MQTT 派工 → `GuideAgent` 在 Blackboard 圖上實際移動 →
事件寫進 Blackboard 後由 IntentionalAgent 決定 retry / replan，指標依機器人實際軌跡計算。
不需要 `run_sensors`，但需要 Neo4j、MQTT 與 LLM 設定。

```powershell
python -X utf8 -m navigation_scenario.live.runner constrained_01_30
python -X utf8 -m navigation_scenario.batch_runner --live --category constrained
```

完整說明見 [`docs/navigation_scenario_runbook.md § 8.6`](../docs/navigation_scenario_runbook.md#86-live-模式以真實-gias-流程執行navigation_scenariolive)。

### 評估指標

| 指標 | 全稱 | 定義 |
|------|------|------|
| **TSR** | Task Success Rate | 案例層級：是否到達全部目標 |
| **ISR** | Intention Stability Ratio | 注入事件後是否仍堅持原意圖（未放寬 constraint、未中斷）|
| **RSR** | Replanning Success Rate | 在需要 replan 的案例中成功完成的比例 |
| **PE** | Path Efficiency | baseline 最短路徑距離 ÷ 實際走過的距離（clip 至 [0, 1]）|

詳細協定、payload 範例與評估表格請見
[`docs/navigation_scenario_runbook.md`](../docs/navigation_scenario_runbook.md)。

## 前置條件

1. Neo4j 啟動，且 `actions` 與 `blackboard` 兩個 database 已建立（或 Enterprise edition 可自動建立）；密碼與 `gias.toml` 一致。
2. `gias.toml` 內 `[llm.openai].api_key` 可用（`seed_actions` 每個 action 需呼叫一次 embedding API，共 7 次）。

## 執行：建立 Action KG 與 Blackboard KG

> Windows PowerShell 範例。為避免主控台無法輸出 emoji，全程加 `-X utf8` 旗標。

```powershell
$py = "C:\Users\mfshi\miniconda3\envs\gias\python.exe"

# Step 1：種子 Action KG（一次即可，除非 actions database 被清空）
& $py -X utf8 -m navigation_scenario.seed_actions

# Step 2：種子 Blackboard KG（每次執行新一輪測試前重置）
& $py -X utf8 -m navigation_scenario.seed_blackboard

# 若 blackboard 累積大量舊資料（例如 >1 萬節點），腳本會自動 DROP/CREATE database。
# 也可手動強制重建：
& $py -X utf8 -m navigation_scenario.seed_blackboard --recreate
```

執行成功會看到類似：

```
=== navigation_scenario.seed_actions ===
  目標 database : actions
  [1] 連線 OK，開始清除舊資料
  [2] 寫入 7 個導航相關 action
    - Locate Exhibit (task=LocateExhibit, ...)
    ...
  [✓] 完成。Actions=7, Params=24

=== navigation_scenario.seed_blackboard ===
  目標 database : blackboard
  [1] 連線 OK，開始建立 baseline 圖譜
  [2] baseline 已建立
  zones=10, pois=9, booths=11, edges=50 (directed), agents=3
  [✓] 完成。
```

## Action KG 內容（7 個導航相關 action）

| Task | 用途 |
|---|---|
| `LocateExhibit` | 引導至指定展區/攤位/展品 |
| `NavigationAssistance` | 移動中即時方向提示 |
| `SuggestRoute` | 路線規劃（支援 `avoid_crowded` / `avoid_closed` / `waypoints`） |
| `ExplainDirections` | 自然語言方向說明 |
| `CrowdStatus` | 查詢人潮狀況 |
| `LocateFacility` | 找廁所/出口/服務台 |
| `ReplanRoute` | **新增**：受動態事件影響時，從目前位置重新規劃前往剩餘目標 |

## Blackboard KG 內容（baseline）

- **Zones (10)**：Main_Hall, AI_Tech_Area, Robotics_Area, Gaming_Area, VR_Area, IoT_Area, Biotech_Area, Startup_Area, Food_Court, Exit_Hall
- **POIs (9)**：含 P_Entrance（所有案例的起點）、P_Info、P_North_Hub、P_South_Hub、P_Cafe、P_Restroom_N/S、P_Bio1、P_Exit
- **Booths (11)**：1–2 個/區
- **CONNECTED_TO 邊**：25 條雙向邊（共 50 個 directed edges），含冗餘路徑供 replan 使用
- **Zone 預設狀態**：全部 `Normal`
- **Agents**：
  - `GuideBot_01` / `GuideBot_02`：起點 P_Entrance / 狀態 Idle
  - `SecBot_Alpha`：起點 P_Info / 狀態 Idle

## ⚠️ 重要：與 experiment1 共用 database

`gias.toml` 的 `actions` / `blackboard` database 是全域設定，本模組與
`experiment1` 共用同一個 Neo4j database，**後執行的 seed 會覆寫前一個**。
切換套件時請重新執行對應的 `seed_actions` 和 `seed_blackboard`。

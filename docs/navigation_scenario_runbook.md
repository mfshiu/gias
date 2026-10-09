# Navigation Scenario 執行說明（Runbook）

本文件說明 `navigation_scenario` 子專案在 Windows + Conda（`gias` 環境）下的
**完整執行流程**：環境檢查 → 建立兩個 KG → 啟動感測器 → 後續測試。

> 對應模組：`navigation_scenario/`
> 對應驗證目標：GIAS 在動態環境（人潮壅塞 / 區域關閉 / 改道）下的**穩定性與適應能力**

---

## 0. 環境檢查與前置條件

| 項目 | 需求 |
|------|------|
| OS | Windows 10/11 |
| Python | 3.12+（建議使用 conda 環境 `gias`） |
| Neo4j | 5.x，**Enterprise** 支援多 database；Community 僅能單一 database |
| MQTT Broker | 與 `gias.toml [broker.mqtt01]` 設定一致（預設 `localhost:1884`） |
| OpenAI API Key | 寫入 `gias.toml [llm.openai].api_key`（`seed_actions` 會用到 embedding） |

### 啟用 conda 環境

```powershell
conda activate gias
cd D:\Work\Gias
```

### 確認 `gias.toml` 必要區段

```toml
[broker.mqtt01]
host = "localhost"
port = 1884

[llm.openai]
api_key = "sk-..."

[kg]
type = "neo4j"

[kg.neo4j]
uri = "bolt://localhost:7687"
user = "neo4j"
password = "Gias1234"
database = "neo4j"

[kg.neo4j_actions]
database = "actions"

[kg.neo4j_blackboard]
database = "blackboard"
```

### 啟動依賴服務

1. **Neo4j Desktop**：啟動 GIAS instance（狀態 = RUNNING）。
2. **MQTT broker**：執行 `mosquitto.bat`（或自行啟動 mosquitto，需與 `gias.toml` 一致）。

---

## 1. 完整執行流程（一覽）

```
┌──────────────────────────────────────────────────────┐
│ Step 1：建立 Action KG （一次即可）                  │
│   python -m navigation_scenario.seed_actions         │
├──────────────────────────────────────────────────────┤
│ Step 2：建立 Blackboard KG baseline（每輪測試前）    │
│   python -m navigation_scenario.seed_blackboard      │
│   # 大量舊資料時：--recreate                          │
├──────────────────────────────────────────────────────┤
│ Step 3：啟動感測器 + Blackboard（可選）              │
│   python -m navigation_scenario.run_sensors          │
└──────────────────────────────────────────────────────┘
```

---

## 2. Step 1 — 建立 Action KG（`seed_actions`）

將 7 個導航相關 Action（含 OpenAI embedding）寫入 `actions` database。

```powershell
python -X utf8 -m navigation_scenario.seed_actions
```

### 成功輸出

```
=== navigation_scenario.seed_actions ===
  目標 database : actions
  [1] 連線 OK，開始清除舊資料
  [2] 寫入 7 個導航相關 action
  [3] 建立 / 確認 vector index (action_desc_vec)
  [✓] 完成。Actions=7, Params=15
```

### Action 一覽

| Task | 說明 |
|------|------|
| `LocateExhibit` | 引導至指定展區/攤位/展品 |
| `NavigationAssistance` | 移動中即時方向提示 |
| `SuggestRoute` | 路線規劃（`avoid_crowded` / `avoid_closed` / `waypoints`） |
| `ExplainDirections` | 自然語言方向說明 |
| `CrowdStatus` | 查詢人潮狀況 |
| `LocateFacility` | 找廁所/出口/服務台 |
| `ReplanRoute` | 動態事件後從目前位置重新規劃 |

> **何時要重跑？** Action KG 變動或 `actions` database 被清空時。
> **副作用**：腳本會清空 `actions` database 中既有的 `Action` / `Param` 節點。

---

## 3. Step 2 — 建立 Blackboard KG baseline（`seed_blackboard`）

每輪測試前重置展場環境到乾淨 baseline（10 zones / 9 POIs / 11 booths / 25 雙向邊）。

```powershell
python -X utf8 -m navigation_scenario.seed_blackboard
```

腳本行為：

1. 確認 `blackboard` database 存在（不存在則 `CREATE DATABASE`，需 Enterprise）。
2. 若既有節點數 < 10,000 → 分批 `DETACH DELETE`。
3. 若 **≥ 10,000** → 自動 `STOP / DROP / CREATE blackboard`（避免拖垮 Neo4j）。
4. 重建 baseline（States / Skills / Zones / POIs / Booths / Edges / Agents）。

### 強制重建

```powershell
python -X utf8 -m navigation_scenario.seed_blackboard --recreate
```

### 成功輸出

```
=== navigation_scenario.seed_blackboard ===
  目標 database : blackboard
  [1] 連線 OK，開始建立 baseline 圖譜
  [2] baseline 已建立
  zones=10, pois=9, booths=11, edges=50 (directed), agents=3
  [✓] 完成。
```

### Baseline 圖譜內容

- **Zones (10)**：Main_Hall, AI_Tech_Area, Robotics_Area, Gaming_Area, VR_Area, IoT_Area, Biotech_Area, Startup_Area, Food_Court, Exit_Hall
- **POIs (9)**：含 `P_Entrance`（所有案例起點）、`P_Info` / `P_North_Hub` / `P_South_Hub` / `P_Cafe` / `P_Restroom_N` / `P_Restroom_S` / `P_Bio1` / `P_Exit`
- **Booths (11)**：每區 1–2 個
- **Edges**：25 條雙向邊（50 directed），含冗餘路徑供 replan 使用
- **Zone 預設狀態**：全部 `Normal`
- **Agents**：`GuideBot_01` / `GuideBot_02`（P_Entrance / Idle）、`SecBot_Alpha`（P_Info / Idle）

---

## 4. Step 3 — 啟動感測器代理（`run_sensors`，可選）

啟動 BlackboardAgent + 4 類感測器代理，各依不同隨機特性持續更新 Blackboard KG。

```powershell
python -X utf8 -m navigation_scenario.run_sensors
python -X utf8 -m navigation_scenario.run_sensors --seed 42
python -X utf8 -m navigation_scenario.run_sensors --seed 42 --bb-poll 1.5
```

### 參數

| 旗標 | 預設 | 說明 |
|------|------|------|
| `--seed` | 42 | RNG 種子，可重現感測序列 |
| `--bb-poll` | 2.0 | BlackboardAgent watcher 輪詢秒數 |
| `--log-level` | （讀 `LOG_LEVEL` 或 `DEBUG`） | 覆寫日誌等級，可選 `VERBOSE` / `DEBUG` / `INFO` / `WARNING` / `ERROR` |

### 日誌等級

| 等級 | 顯示內容 |
|------|----------|
| `INFO`（藍色 `I`） | 中文白話的感測行為描述，例如：<br>`[sensor:visual] 視覺感測：偵測到通道 P_South_Hub → B_GM1 出現人潮阻擋，將該邊（雙向）標記為封鎖。` |
| `VERBOSE`（淺灰 `V`） | 額外輸出實際 Cypher 一行版本與參數；長 `rows` 自動截斷顯示前 3 筆並附 `...(+N more)` |

範例：

```powershell
# 操作員視角：只看每個感測器做了什麼
python -X utf8 -m navigation_scenario.run_sensors --log-level INFO

# 開發/除錯視角：同時看到 Cypher 與參數
python -X utf8 -m navigation_scenario.run_sensors --log-level VERBOSE
```

### 感測器特性對照（皆為 idempotent `write`，**不建 Observation**）

| 感測器 | 類別 | 聯絡機率 | 輪詢秒 | 更新方式 |
|--------|------|---------:|------:|----------|
| `PedestrianFlowSensorAgent` | 人流／動線 | 0.55 | ~2s | UNWIND 更新 Zone `CURRENT_STATE` + 屬性 |
| `FacilityEventSensorAgent` | 設施／展位 | 0.40 | ~3s | UNWIND 更新 Booth.status / POI.facility_status |
| `VisualSensorAgent` | 視覺／通道 | 0.28 | ~1.8s | SET `CONNECTED_TO.blocked`（**僅狀態變化時**才送） |
| `DigitalSensorAgent` | 數位／API | 0.38 | ~4s | 重建 Zone `CURRENT_STATE`（同狀態則機率跳過） |

> **隨機聯絡邏輯**：每個輪詢週期以 `contact_probability` 抽樣決定是否聯絡
> BlackboardAgent。所有 Cypher 皆為 `MATCH + SET / MERGE` 既有節點，
> 不會建立 `Observation` 歷史節點，長時間運行不會讓 Blackboard 膨脹。

### 結束

`Ctrl + C` 結束；感測器 thread 為 daemon，主程式退出即關閉。

### 動態調整：MQTT 控制協定

感測器啟動後會訂閱 3 層控制 topic，**等待其他 Agent**（例如後續會建立的
`event_injector`）發送訊息來：
- 變更感應頻率（`set_interval`、`set_contact_probability`）
- 變更隨機分布與機率（`set_random_param`）
- 注入動態事件並立即生效（`inject_event`）

感測器只負責「接收 → 套用 → 回 ACK」；不發送變更請求。詳細協定請參考
[§ 5. 感測器控制協定](#5-感測器控制協定mqtt)。

---

## 5. 感測器控制協定（MQTT）

所有感測器繼承 `ScenarioSensorAgent`，啟動後自動訂閱以下 3 層 topic：

| 層級 | Topic | 用途 |
|------|-------|------|
| 單一實例 | `navigation_scenario.sensor.<sensor_id>.control` | 只控制某一個 instance |
| 同類廣播 | `navigation_scenario.sensors.<sensor_kind>.control` | 控制所有同類感測器（例：所有 `visual`） |
| 全體廣播 | `navigation_scenario.sensors.control` | 控制所有感測器（不相關 kind 會自動跳過） |

`<sensor_kind>` 可為：`pedestrian_flow` / `facility_event` / `visual` / `digital`。

訊息格式為 AgentFlow Parcel（`text/json|{...}`）；`content` 為 dict，固定欄位
`command` 表示要執行的指令。發送 Agent 只要呼叫 `self.publish(topic, payload)`，
AgentFlow 會自動包裝。

### 5.1 共通指令

| Command | Payload 欄位 | 行為 |
|---------|-----------|------|
| `status` | – | 回傳目前狀態（含 `random_params`） |
| `set_contact_probability` | `probability`: 0–1 | 改變每輪詢嘗試聯絡 Blackboard 的機率 |
| `set_interval` | `interval`: 秒 (>= 0.2) | 改變輪詢間隔 |
| `pause` / `resume` | – | 暫停 / 恢復隨機輪詢（覆寫 inject 仍會立即套用） |
| `sample_now` | – | 立即抽一次樣本並寫入 KG（測試用） |
| `clear_overrides` | – | 清除所有尚未消費的事件覆寫 |
| `set_random_param` | `name`, `value` | 變更該感測器特有的隨機數值（見 §5.3） |
| `inject_event` | `kind`, `target`, `ttl_samples?`, `payload?` | 注入動態事件，**立即**寫進 Blackboard |

### 5.2 `inject_event` 事件對照表

| event kind | 由誰處理 | `target` 格式 | 對 Blackboard 的影響 |
|-----------|---------|--------------|---------------------|
| `crowd_congestion` | `pedestrian_flow` | Zone 名稱字串或字串列表 | Zone `CURRENT_STATE` → `Crowded`，density ≈ 0.9 |
| `crowd_clear` | `pedestrian_flow` | 同上 | Zone → `Normal` |
| `crowd_sparse` | `pedestrian_flow` | 同上 | Zone → `Sparse` |
| `route_detour` | `visual` | `{"from": "X", "to": "Y"}` 或 `"X->Y"` | `CONNECTED_TO.blocked=true`（雙向） |
| `route_clear` | `visual` | 同上 | `CONNECTED_TO.blocked=false` |
| `area_closure` | `digital` | Zone 名稱字串 | Zone → `Closed`，`api_event=zone_closure` |
| `area_reopen` | `digital` | Zone 名稱 | Zone → `Normal`，`api_event=event_cancellation` |
| `crowd_alert` | `digital` | Zone 名稱 | Zone → `Crowded`，`api_event=crowd_alert` |
| `route_advisory` | `digital` | Zone 名稱 | Zone → `Normal`，`api_event=route_advisory` |
| `booth_closure` | `facility_event` | Booth id 字串或列表 | `Booth.status=closed` |
| `booth_reopen` | `facility_event` | 同上 | `Booth.status=open` |
| `facility_outage` | `facility_event` | POI id | `POI.facility_status=unavailable` |
| `facility_restore` | `facility_event` | POI id | `POI.facility_status=available` |

`ttl_samples`（預設 1）：此覆寫會被消費幾次。設 `3` 代表接下來 3 次 `sample_update`
都會強制套用，模擬「持續事件」。

> **同訊息可廣播給多個感測器**：把訊息發到 `navigation_scenario.sensors.control`，
> 每個感測器都會收到；不在自己 `HANDLED_EVENT_KINDS` 內的 `kind` 會回
> `{"ok": true, "accepted": false, "skipped": true}` 而不會誤動作。

### 5.3 各感測器的隨機參數（給 `set_random_param`）

| 感測器 | name | value 型別 | 說明 |
|--------|------|-----------|------|
| `pedestrian_flow` | `crowd_weights` | `[c, n, s]` 三個 ≥ 0 數值 | 對 `[Crowded, Normal, Sparse]` 的抽樣權重 |
| `pedestrian_flow` | `resample_probability` | 0–1 | 每個 Zone 重新抽樣（vs 沿用上次）的機率 |
| `visual` | `block_probability` | 0–1 | 一條未封鎖邊在這次抽樣中被封鎖的機率 |
| `visual` | `clear_probability` | 0–1 | 一條已封鎖邊在這次抽樣中被解除的機率 |
| `digital` | `api_event_weights` | `[w1, w2, w3, w4]` | 對應 `[zone_closure, crowd_alert, route_advisory, event_cancellation]` |
| `digital` | `skip_unchanged_probability` | 0–1 | 同狀態時跳過寫入的機率（防 idle 大量無意義 write） |
| `facility_event` | `booth_status_weights` | `[w1, w2, w3]` | `[open, open, closed]` 的抽樣權重（共 3 個） |
| `facility_event` | `booth_resample_probability` | 0–1 | 每個展位重抽機率 |
| `facility_event` | `facility_outage_probability` | 0–1 | 設施暫時不可用的機率 |

### 5.4 範例：從其他 AgentFlow Agent 發送

```python
# 在外部 Agent 內呼叫
from navigation_scenario.sensors.base import SENSORS_BROADCAST_TOPIC

# (A) 全體廣播：注入 AI 區人潮壅塞，持續 3 次 sample
self.publish(SENSORS_BROADCAST_TOPIC, {
    "command": "inject_event",
    "kind": "crowd_congestion",
    "target": "AI_Tech_Area",
    "ttl_samples": 3,
})

# (B) 只給 visual 感測器：封鎖 B_RB1→B_RB2
self.publish("navigation_scenario.sensors.visual.control", {
    "command": "inject_event",
    "kind": "route_detour",
    "target": {"from": "B_RB1", "to": "B_RB2"},
    "payload": {"obstacle_type": "construction"},
})

# (C) 提高 pedestrian_flow 的擁擠抽樣權重
self.publish("navigation_scenario.sensors.pedestrian_flow.control", {
    "command": "set_random_param",
    "name": "crowd_weights",
    "value": [0.7, 0.2, 0.1],   # 大幅提高 Crowded 機率
})

# (D) 暫時降低 visual 感測器頻率
self.publish("navigation_scenario.sensors.visual.control", {
    "command": "set_interval",
    "interval": 5.0,
})
```

> 接收端的回應透過 AgentFlow 的 `publish_sync` 機制取得 ACK（含
> `accepted`、`sent` 等欄位），單純 `publish` 則無需等待回應。

### 5.5 用 Neo4j Browser 驗證注入是否生效

```cypher
// 5.5a 看 Zone 目前狀態（注入 area_closure / crowd_congestion 後應反映）
MATCH (z:Zone)-[r:CURRENT_STATE]->(s:State)
RETURN z.name AS zone, s.status_name AS state, r.source AS source,
       r.api_event AS api_event, r.updated_at AS updated_at
ORDER BY r.updated_at IS NULL, r.updated_at DESC;

// 5.5b 看被封鎖的通道（注入 route_detour 後應出現）
MATCH (a)-[r:CONNECTED_TO]->(b)
WHERE r.blocked = true
RETURN a.id AS from, b.id AS to, r.obstacle_type AS obstacle,
       r.last_sensor_update AS updated_at
ORDER BY r.last_sensor_update DESC;

// 5.5c 看展位狀態（注入 booth_closure 後應反映）
MATCH (b:Booth)
RETURN b.id, b.status, b.last_sensor_update
ORDER BY b.last_sensor_update IS NULL, b.last_sensor_update DESC;
```

---

## 6. 驗證資料是否確實寫入 KG

> 註：與感測器控制注入相關的查詢請見 §5.5。

### 在 Neo4j Browser 查詢

切到 `blackboard` database：

```cypher
// Zone 與其目前狀態
MATCH (z:Zone)-[:CURRENT_STATE]->(s:State)
RETURN z.name AS zone, s.status_name AS state
ORDER BY zone;

// 被封鎖的通道
MATCH (a)-[r:CONNECTED_TO]->(b)
WHERE r.blocked = true
RETURN a.id AS from, b.id AS to, r.distance AS dist;

// 展位狀態
MATCH (b:Booth)
RETURN b.id AS id, b.exhibitor AS exhibitor, b.status AS status;

// 確認 Observation 數量（本套件運行後應為 0，除非曾跑過 src.run_observers）
MATCH (o:Observation) RETURN count(o) AS observations;
```

切到 `actions` database：

```cypher
MATCH (a:Action) RETURN a.task AS task, a.topic AS topic ORDER BY task;
```

---

## 7. 常見問題與排錯

### Q1. `seed_blackboard` 卡住 / 連線中斷（`ServiceUnavailable`）

原因：blackboard database 累積大量舊資料，單一 `DETACH DELETE` 把 Neo4j 拖垮。

對策：

```powershell
python -X utf8 -m navigation_scenario.seed_blackboard --recreate
```

或在 Neo4j Browser（`system` database）手動：

```cypher
:use system
STOP DATABASE blackboard;
DROP DATABASE blackboard;
CREATE DATABASE blackboard;
```

### Q2. Neo4j Desktop 卡在 `STOPPING`

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq "java.exe" -and ($_.CommandLine -match "neo4j") } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Get-NetTCPConnection -LocalPort 7687 -ErrorAction SilentlyContinue
```

### Q3. `seed_actions` 失敗，提示缺少 LLM 設定

確認 `gias.toml` 內 `[llm]` 與 `[llm.openai].api_key` 正確；該腳本對每個 action 呼叫一次 embedding（共 7 次）。

### Q4. Neo4j Community Edition 不支援多 database

把 `gias.toml` 改為：

```toml
[kg.neo4j_actions]
database = "neo4j"

[kg.neo4j_blackboard]
database = "neo4j"
```

兩套資料會混在一起，需自行用標籤區分。

### Q5. 主控台顯示亂碼

務必加 `-X utf8`：

```powershell
python -X utf8 -m navigation_scenario.seed_actions
```

或：`$env:PYTHONIOENCODING = "utf-8"`

### Q6. 與 `experiment1` 共用 database 互相覆寫

`gias.toml` 的 `actions` / `blackboard` 是全域設定；切換子專案前重新執行對應的 `seed_actions` 與 `seed_blackboard`。

### Q7. 感測器跑久了會不會讓 Blackboard 暴增？

**不會**。`navigation_scenario.sensors` 全部都採 idempotent `write`，僅
`MATCH + SET / MERGE` 既有 Zone / Booth / Edge，**完全不送 `observe`、
不建立 `Observation` / `ObservedEntity` 節點**，所以節點總數會維持在
baseline 規模附近。

> 對照：`src/observer/*` 的原始 `ObserverAgent` 預設會送 `observe`，
> `BlackboardAgent._handle_observe` 內部 `MERGE (o:Observation {observation_id})`
> 但 `observation_id` 是新 UUID → 等同 CREATE，**會持續累積**。
> 這也是先前 blackboard 出現 37 萬節點的原因。

若 Blackboard 仍有舊 Observation 資料（曾跑過 `src.run_observers` 或
`src.run_navigation_with_observers`）：

```powershell
python -X utf8 -m navigation_scenario.seed_blackboard --recreate
```

或在 Neo4j Browser 手動清：

```cypher
MATCH (o:Observation)
OPTIONAL MATCH (o)-[:OBSERVED]->(e:ObservedEntity)
DETACH DELETE o, e;
```

---

## 8. 測試案例執行與評估

### 8.1 模組總覽

| 模組 | 角色 |
|------|------|
| `test_cases.py` | 60 個案例定義（單目標 20、限制式 20、多步驟 20，30%/60% 各 30） |
| `pathing.py` | 從 Blackboard KG 抓圖 + Dijkstra（支援 avoid_crowded / avoid_closed / avoid_blocked） |
| `event_injector.py` | paho-mqtt 注入器：向感測器發 `inject_event` |
| `metrics.py` | **TSR / ISR / RSR / PE** 指標定義與彙整 |
| `runner.py` | 單一案例執行器：抓 baseline 路徑 → 走到 30%/60% 注入 → replan → 完成 |
| `batch_runner.py` | 批次執行（全部 / 子集），結果寫到 `navigation_scenario_results/run_<ts>/` |
| `analyze.py` | 彙整 → `summary.csv` / `aggregate.csv` / `report.md` |

### 8.2 評估指標

| 指標 | 定義 | 對應 GIAS 能力 |
|------|------|---------------|
| **TSR** (Task Success Rate) | 是否到達全部目標 | 整體完成率 |
| **ISR** (Intention Stability) | 注入動態事件後是否仍堅持原意圖（未放寬 constraint、未中斷）| **Intention Stability** |
| **RSR** (Replanning Success) | 在需要 replan 的案例中成功完成的比例 | **Dynamic Adaptation** |
| **PE** (Path Efficiency) | baseline 最短距離 ÷ 實際距離；clip 至 [0,1] | **Plan Executability** |

### 8.3 執行流程

```powershell
# 前置：seed_blackboard 已完成 + MQTT broker + run_sensors 已啟動
# Terminal A：保留 run_sensors 持續運作
python -X utf8 -m navigation_scenario.run_sensors --seed 42

# Terminal B：跑單一案例
python -X utf8 -m navigation_scenario.runner single_01_30 --output single_01_30.json

# Terminal B：跑完 60 個案例 + 自動分析
# 預設會在「批次前」與「每案之間」呼叫 kg_reset 還原封鎖邊與 Zone 狀態
python -X utf8 -m navigation_scenario.batch_runner

# 僅在除錯「累積污染」行為時才關閉還原（成功率會大幅下降）
python -X utf8 -m navigation_scenario.batch_runner --no-reset-between

# 篩選類別 / 注入時機 / 事件種類
python -X utf8 -m navigation_scenario.batch_runner --category multi_step
python -X utf8 -m navigation_scenario.batch_runner --pct 60
python -X utf8 -m navigation_scenario.batch_runner --event route_detour

# 只重新分析已存結果
python -X utf8 -m navigation_scenario.analyze navigation_scenario_results/run_20260524_193000
```

### 8.4 Runner 的單案例流程

1. 載入 Blackboard KG 快照
2. 計算 baseline 最短路徑（含案例 constraints）
3. 沿路徑前進（每節點一 step）
4. 進度達 `injection_pct` 時：
   - 透過 `MqttEventInjector` 發送 `inject_event`
   - 等 `snapshot_wait_sec`（預設 2s）讓感測器寫入 KG
   - 重抓快照、從目前節點 replan 到剩餘目標
   - 若 replan 失敗 → 漸進放寬軟限制（先去掉 `avoid_crowded`，再去掉 `avoid_blocked`）；
     一旦放寬 → `ISR=0`（意圖部分妥協）
5. 任務結束時自動送 recovery event（`route_clear` / `area_reopen` / `crowd_clear`）
6. 產出 `CaseMetrics` 與 `StepRecord[]`

**批次成功率（對齊簡報表）**

| 現象 | 原因 | 處理 |
|------|------|------|
| 大量 `baseline path unreachable`、約 2.1s 就 FAIL | 60 案共用同一 KG，前一案 MQTT/感測器留下的 `blocked` 邊未清乾淨 | 使用預設 `batch_runner`（內建 `kg_reset`），或跑前 `seed_blackboard` |
| ISR/PE 極低但 RSR 顯示 100% | 在 baseline 就失敗，`replan_required=false` 時 RSR 不計入分母 | 先修 baseline，再看 RSR |
| `kg_reset` 出現 Neo4j MemoryPool OOM | 交易記憶體上限 + 感測器同時寫入 | 批次期間暫停 `run_sensors` 的隨機封邊；提高 `dbms.memory.transaction.total.max`；必要時每批前 `seed_blackboard` |
| 簡報 Dynamic Adaptation 仍略低於 88% | 部分案例 `replan_required` 但 replan 未標記成功（路徑仍可走通） | 調整 `runner` replan 判定或案例事件/constraint 配對 |

### 8.5 結果檔案

```
navigation_scenario_results/
  run_20260524_193000/
    _manifest.json              # 本次執行摘要
    single_01_30.json           # 每個案例一份結果
    single_01_60.json
    ...
    summary.csv                 # 60 列：每案一行
    aggregate.csv               # 6 列：3 類別 × 2 注入時機
    report.md                   # 與簡報相同格式的 Markdown 報告
```

`report.md` 內容包含：
- 整體 TSR / ISR / RSR / PE
- 「類別 × 注入時機」表格（對應第二張投影片）
- 「事件種類」分類表（crowd_congestion / area_closure / route_detour）
- 失敗案例列表（含 `notes` 欄位）
- `Mode`：`simulation`（圖論模擬）或 `live`（真實 GIAS 流程）

### 8.6 live 模式：以真實 GIAS 流程執行（`navigation_scenario.live`）

8.4 的 runner 是圖論模擬，不會呼叫 IntentionalAgent。live 模式讓每個案例走完整條管線，
作為端到端 benchmark 與回歸測試：

1. `IntentionalAgent.plan_intention(案例請求)`：真實 LLM + Action KG 規劃
2. `execute_plan_with_monitoring`：監測迴圈經 MQTT 派工
3. `GuideAgent`（取代 NavigationAgent）在 Blackboard 圖上**實際移動**：每走一條邊前重讀圖、重算路徑；
   封鎖通道與 Closed 區域不可通行，`avoid_crowded` 依任務參數；走不到時回報失敗
4. 機器人已走邊數達 baseline 邊數的 `injection_pct` 時，把事件**直接寫進 Blackboard**；
   BlackboardAgent 偵測後通知 IntentionalAgent，由它決定 retry / repair / replan
5. 依機器人實際軌跡計算指標

**前置條件**：MQTT broker、Neo4j（`actions` 已用本套件的 `seed_actions` 建立、`blackboard` 已 seed）、
`gias.toml` 的 LLM 設定。**不需要** `run_sensors`；BlackboardAgent、GuideAgent、InfoAgent 由 live 模式在同一個
process 啟動，請勿同時執行其他 NavigationAgent（例如 `run_all`），否則會重複接單。

```powershell
# 單一案例
python -X utf8 -m navigation_scenario.live.runner constrained_01_30 --output c01.json

# 批次 + 自動分析（其餘篩選參數與模擬模式相同）
python -X utf8 -m navigation_scenario.batch_runner --live --category constrained

# 啟用 IntentionalAgent 的 LLM 輔助判斷；機器人走慢一點（每公尺 0.2 秒）
python -X utf8 -m navigation_scenario.batch_runner --live --llm-assist --seconds-per-meter 0.2

# 改用感測器注入事件（需 run_sensors；run_sensors 已啟動 BlackboardAgent，所以加 --no-blackboard-agent）
python -X utf8 -m navigation_scenario.batch_runner --live --via-sensors --no-blackboard-agent
```

| 參數 | 預設 | 說明 |
|------|------|------|
| `--seconds-per-meter` | 0.1 | 機器人移動速度（10 公尺的邊走 1 秒） |
| `--case-timeout` | 180 | 單一案例逾時（秒，含 LLM 規劃）；逾時會要求 IntentionalAgent 停止並取消執行中的動作 |
| `--node-timeout` | 60 | 單一動作逾時（秒） |
| `--llm-assist` | 關 | `intent.monitoring.enable_llm_assist` |
| `--via-sensors` | 關 | 經 MQTT 由感測器注入（會受感測器隨機性影響） |

**live 模式的指標定義**（與模擬模式使用相同欄位，資料來源不同）：

| 指標 | live 模式的定義 |
|------|----------------|
| TSR | 機器人依序到達全部目標（zone 目標：走進該區任一節點） |
| ISR | 任務成功，且途中沒有違反案例限制（例：要求避開擁擠卻走進 Crowded 區；目標所在區除外） |
| RSR | 事件注入後 IntentionalAgent 做了 retry / repair / replan，或 GuideAgent 繞行，且最後成功 |
| PE | baseline 最短距離 ÷ 機器人實際走過的距離（多目標時逐段平均） |
| 完成時間 | 真實經過時間（含 LLM 規劃）；不同 `--seconds-per-meter` 之間不可比較 |

每個案例的 JSON 在 `metrics.extra` 另有：`plan_steps`（LLM 規劃出的步驟）、`planning_sec`、`ia_replans`、
`ia_redispatches`、`replan_log`、`executor_reroutes_after_injection`、`constraint_violations`、`injection`
（注入時機器人位置與進度）等，用來判斷調適發生在意圖層還是執行層。

> 注意：BlackboardWatcher 不監看關係屬性，`route_detour`（`CONNECTED_TO.blocked`）不會產生事件，
> 只會由 GuideAgent 在下一步就地繞行。

---

## 9. 一鍵跑腳本（範例）

```powershell
$py = "C:\Users\mfshi\miniconda3\envs\gias\python.exe"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "=== Step 1: seed_actions ===" -ForegroundColor Cyan
& $py -X utf8 -m navigation_scenario.seed_actions
if ($LASTEXITCODE -ne 0) { throw "seed_actions failed" }

Write-Host "`n=== Step 2: seed_blackboard ===" -ForegroundColor Cyan
& $py -X utf8 -m navigation_scenario.seed_blackboard --recreate
if ($LASTEXITCODE -ne 0) { throw "seed_blackboard failed" }

Write-Host "`n=== Step 3: run_sensors（Ctrl+C 結束） ===" -ForegroundColor Cyan
& $py -X utf8 -m navigation_scenario.run_sensors --seed 42
```

---

## 10. 模組對照表

| 模組 / 檔案 | 角色 |
|-------------|------|
| `navigation_scenario/__init__.py` | 套件入口、模組說明 |
| `navigation_scenario/config.py` | 展場地圖、Domain Profile、事件常數 |
| `navigation_scenario/seed_actions.py` | 寫入 Action KG（→ `actions` DB） |
| `navigation_scenario/seed_blackboard.py` | 寫入 Blackboard KG（→ `blackboard` DB），含 `--recreate` |
| `navigation_scenario/run_sensors.py` | 啟動 Blackboard + 4 類感測器 |
| `navigation_scenario/sensors/base.py` | `ScenarioSensorAgent` 基礎類別（支援單一/多語句 write） |
| `navigation_scenario/sensors/pedestrian_flow.py` | 人流／動線感測（UNWIND 寫 Zone 狀態） |
| `navigation_scenario/sensors/facility_event.py` | 設施／展位感測（UNWIND 寫 Booth/POI） |
| `navigation_scenario/sensors/visual.py` | 視覺／通道封鎖感測（變化才寫 edge） |
| `navigation_scenario/sensors/digital.py` | 數位／場館 API 感測（同狀態機率跳過） |
| `navigation_scenario/README.md` | 模組概觀（精簡版） |
| `navigation_scenario/live/` | live 模式：GuideAgent、事件注入、指標計算、`LiveSession` 與 CLI（見 8.6） |
| `docs/navigation_scenario_runbook.md` | 本文件（完整執行手冊） |
| `docs/monitoring_protocol.md` | Topic / payload 契約（IntentionalAgent ↔ executors） |

# Experiment 1：展場導航測試（5.3.5 Test Cases）

依論文 §5.3.5 設計三組共 **60 個** 測試案例，於 **30% / 60%** 兩種事件注入比例下，驗證 GIAS 在意圖處理、環境適應、任務執行三方面的能力。

| 組別 | 案例數 | 範例 | 主要 metrics |
|---|---|---|---|
| **Single-Goal** | 20 | 「請帶我去 AI 展區。」 | TSR, PE |
| **Constraint-Based** | 20 | 「請帶我去 AI 展區，避開擁擠區域。」 | TSR, ISR, RSR |
| **Multi-Step** | 20 | 「先去機器人展區，再去 AI 展區，避開封閉區域。」 | ISR, RSR, PE |

動態事件：`crowd_congestion` / `area_closure` / `route_detour`，依 `--ratios` 機率注入到任務開始前與執行中段。

## 模組架構

```
experiment1/
  config.py          # 圖譜環境、Domain Profile、常數
  test_cases.py      # 60 個測試案例定義
  seed_actions.py    # 寫入 actions DB 的執行程式
  seed_blackboard.py # 寫入 blackboard DB 的 baseline 圖譜
  event_injector.py  # 動態事件注入（30%/60%）
  runner.py          # 單一 case 執行核心（plan + 模擬導航 + replan）
  batch_runner.py    # 共用批次執行 + 結果寫檔
  metrics.py         # CaseResult / Aggregate（TSR/PE/ISR/RSR）
  run_single_goal.py # Group A 執行器
  run_constraint.py  # Group B 執行器
  run_multi_step.py  # Group C 執行器
  analyze.py         # 結果彙總分析（CSV + Markdown）
  results/           # 輸出目錄
```

## 前置條件

1. Neo4j（含 `actions` 與 `blackboard` 兩個 database）已啟動，密碼與 `gias.toml` 一致。
2. MQTT broker 啟動（port `1884`）— 僅 `IntentionalAgent` 自身連線時需要，模擬導航不依賴 broker。
3. `gias.toml` 內 `[llm.openai].api_key` 可用（每 case ~3 次 LLM 呼叫）。

## 執行流程（完整 60 案例 × 2 比例）

> Windows PowerShell 範例。為避免 Windows 主控台無法輸出 emoji，全程加 `-X utf8` 旗標。

### 1. 種子 actions（一次即可，除非 actions DB 被清空）

```powershell
& "C:\Users\mfshi\miniconda3\envs\gias\python.exe" -X utf8 -m experiment1.seed_actions
```

### 2. 種子 blackboard（每次執行新一輪測試前重置）

```powershell
& "C:\Users\mfshi\miniconda3\envs\gias\python.exe" -X utf8 -m experiment1.seed_blackboard
```

### 3. 跑三組測試（各 20 案例 × 2 比例 = 共 120 次規劃）

```powershell
$env:PYTHONIOENCODING="utf-8"
$py = "C:\Users\mfshi\miniconda3\envs\gias\python.exe"

& $py -X utf8 -m experiment1.run_single_goal --ratios 0.3,0.6
& $py -X utf8 -m experiment1.run_constraint  --ratios 0.3,0.6
& $py -X utf8 -m experiment1.run_multi_step  --ratios 0.3,0.6
```

每個執行器支援的旗標：
- `--ratios`：事件注入比例（逗號分隔），預設 `0.3,0.6`
- `--limit N`：每個 ratio 限制執行前 N 個 case（debug 用）
- `--seed K`：RNG 種子，預設 42（可重現）
- `--no-midway`：停用中途事件注入（不觸發 replan）
- `--out PATH`：自訂輸出 JSON 路徑

### 4. 彙總分析

```powershell
& $py -X utf8 -m experiment1.analyze
```

預設讀取 `experiment1/results/` 下每組最新一份 JSON，產生：
- 終端機表格
- `experiment1/results/summary.csv`
- `experiment1/results/summary.md`

也可顯式指定檔案：

```powershell
& $py -X utf8 -m experiment1.analyze experiment1/results/single_goal_*.json
```

## Smoke 測試（快速驗證模組可運行）

```powershell
& $py -X utf8 -m experiment1.seed_blackboard
& $py -X utf8 -m experiment1.run_single_goal --ratios 0.3 --limit 2
& $py -X utf8 -m experiment1.run_constraint  --ratios 0.6 --limit 2
& $py -X utf8 -m experiment1.run_multi_step  --ratios 0.6 --limit 2
& $py -X utf8 -m experiment1.analyze
```

## 指標定義

| 指標 | 定義 |
|---|---|
| **TSR**（Task Success Rate） | 模擬最終是否到達所有 goal zones |
| **PE**（Plan Executability） | plan 不是 `leaf_unresolved`、含至少 1 個導航類 atomic action、且全在 allowed 集合內 |
| **ISR**（Intention Stability Rate） | 多目標案例中，所有 goal zones 是否依**順序**到訪 |
| **RSR**（Replanning Success Rate） | 中途事件導致 replan 觸發時，新路徑是否能達成剩餘目標。無 replan 時為 `N/A` |
| `constraint_rate` | 受限案例中，避開擁擠/封閉的限制是否實際被滿足 |
| `avg_path_m` | 平均路徑長度（公尺） |
| `avg_time_s` | 平均完成時間（秒，含 replan 懲罰） |

## 模擬規則摘要

- 圖譜：6 zones、6 POIs、7 booths、15 條雙向邊（共 30 個 directed edges）。
- 路徑搜尋：Dijkstra；遇到 `Closed` 區一律不可進入；`Crowded` 區只在啟用 `avoid_crowded` 時不可進入；`route_detour` 把對應邊 `blocked=true`。
- 起點：所有 case 從 `P_Entrance` 開始。
- 速度：`1 m/s`；replan 懲罰 `5 sec`。
- 中途事件：在路徑中點注入；若使既有路徑失效則觸發 replan。
- 為避免起點/終點 anchor 被 detour 孤立，這些節點在 `event_injector` 中被 `protected_nodes` 保護。

## 輸出 JSON Schema（`results/<group>_<ts>.json`）

```jsonc
{
  "group": "single_goal",
  "ratios": [0.3, 0.6],
  "n_cases": 20,
  "midway_inject": true,
  "seed": 42,
  "elapsed_sec": 1234.5,
  "results": [
    {
      "case_id": "SG-01",
      "group": "single_goal",
      "intention": "請帶我去AI 展區。",
      "ratio": 0.3,
      "success": true,
      "plan_executable": true,
      "intention_satisfied": true,
      "replan_triggered": 0,
      "replan_success": 0,
      "constraint_satisfied": true,
      "completion_time_sec": 60.0,
      "path_length_m": 60.0,
      "visited_zones": ["AI_Tech_Area"],
      "plan_atomic_actions": ["LocateExhibit"],
      "initial_events": [...],
      "midway_events": [...],
      "plan_unresolved_reason": "",
      "error": ""
    }
  ]
}
```

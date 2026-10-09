# GIAS Monitoring & Replanning Protocol (v1)

本文件凍結 IntentionalAgent 監測 + 再思考重構所需的 **topic 與 payload schema**，作為 IntentionalAgent / InfoAgent / NavigationAgent / BlackboardAgent 之間的契約。

設計原則：

- 完全用 AgentFlow 既有的 `publish`（async）/ `subscribe` 機制，不更動框架。
- 與既有 `publish_sync` 同步呼叫路徑**完全向後相容**：舊 payload（無 `task_id`）走原路；新 payload（帶 `task_id`）走 async + cancel 路徑。
- 所有事件以 dict / JSON 序列化，採 snake_case；`task_id` 為冪等鍵之一。

---

## 1. Topic 命名

| Topic | 方向 | 用途 |
|-------|------|------|
| `info.request` | IntentionalAgent → InfoAgent | 派工（既有） |
| `navigation.request` | IntentionalAgent → NavigationAgent | 派工（既有） |
| `info.result` | InfoAgent → IntentionalAgent | 任務結果（新增） |
| `navigation.result` | NavigationAgent → IntentionalAgent | 任務結果（新增） |
| `info.cancel` | IntentionalAgent → InfoAgent | 取消指令（新增） |
| `navigation.cancel` | IntentionalAgent → NavigationAgent | 取消指令（新增） |
| `info.progress` | InfoAgent → IntentionalAgent | 進度回報（新增，選用） |
| `navigation.progress` | NavigationAgent → IntentionalAgent | 進度回報（新增，選用） |
| `blackboard.control` | * → BlackboardAgent | 訂閱 / 查詢 / 寫入（既有） |
| `blackboard.subscriber.<requester_id>` | BlackboardAgent → 訂閱者 | 環境變更通知（既有） |

---

## 2. Request payload（`info.request` / `navigation.request`）

新欄位 **皆為選填**；缺少時退回舊行為。

```json
{
  "task": "LocateExhibit",
  "params": { "target_name": "A12", "current_location": "入口" },
  "action_id": "act-001",
  "intent": "帶我去A12攤位",

  "task_id": "uuid-or-node-task-id",
  "idempotency_key": "agent42:node_l3-2-1:v1",
  "deadline_sec": 30,
  "interruptible": true,
  "side_effect": "none"
}
```

| 欄位 | 必填 | 說明 |
|------|------|------|
| `task` | 是 | 動作名稱（與 `Action.task` 對應） |
| `params` | 是 | 動作參數（snake_case） |
| `action_id` | 否 | KG `Action.id`，用於追蹤 |
| `intent` | 否 | 對應子意圖文字，供 log |
| `task_id` | **新** | 任務唯一識別；若提供，視為**async 路徑**，必須回 `*.result` |
| `idempotency_key` | **新** | 冪等鍵；executor 端可去重 |
| `deadline_sec` | **新** | 任務截止秒數（從收到請求起算） |
| `interruptible` | **新** | 是否可中斷（false 時 executor 不理會 cancel 或走 graceful 收尾） |
| `side_effect` | **新** | `none` / `read_only` / `physical` / `external_call`；供 trigger 決策 |

向後相容：若 payload 無 `task_id`，executor 仍依舊回傳 dict 給 `publish_sync` 的 auto-reply 通道。

---

## 3. Result payload（`info.result` / `navigation.result`）

```json
{
  "task_id": "uuid-or-node-task-id",
  "task": "LocateExhibit",
  "ok": true,
  "message": "已規劃從 入口 前往 A12 的路線。",
  "result": { "...": "executor-specific" },
  "cancelled": false,
  "error": null,
  "started_at": "2026-05-24T11:00:00Z",
  "finished_at": "2026-05-24T11:00:05Z",
  "elapsed_sec": 5.0
}
```

| 欄位 | 必填 | 說明 |
|------|------|------|
| `task_id` | 是 | 對應 request 的 `task_id`，IA 用以路由 |
| `ok` | 是 | 是否成功 |
| `cancelled` | 是 | 是否因 cancel 而結束（即使 `ok=true`） |
| `task`, `message`, `result`, `error` | 否 | 業務內容 |
| `started_at`, `finished_at`, `elapsed_sec` | 否 | 計時 |

---

## 4. Cancel payload（`info.cancel` / `navigation.cancel`）

```json
{
  "task_id": "uuid-or-node-task-id",
  "reason": "env_change:Zone/AI_Tech_Area/Closed"
}
```

Executor 行為：

- `task_id` 不在 in-flight 表 → 丟棄（不視為錯誤）
- `task_id` 存在且 `interruptible=true` → 設取消旗標，loop 下個 check point 早退
- 早退後仍須 publish `*.result` 並標記 `cancelled=true`，IA 才能完成生命週期

---

## 5. Progress payload（`info.progress` / `navigation.progress`，選用）

```json
{
  "task_id": "uuid-or-node-task-id",
  "state": "running",
  "ratio": 0.4,
  "note": "查黑板第 2 次"
}
```

`state ∈ {started, running, finishing, done, cancelled, failed}`；progress 是 advisory，IA 不應依賴其抵達。

---

## 6. Plan 節點擴充欄位（all optional）

| 欄位 | 由誰填 | 用途 |
|------|--------|------|
| `task_id` | IA 在 dispatch 時生成 | 任務識別 |
| `idempotency_key` | IA 在 dispatch 時生成 | executor 去重 |
| `attempts` | IA cursor 追蹤 | 失敗重試次數 |
| `state` | IA cursor 追蹤 | PENDING/IN_FLIGHT/DONE/FAILED/CANCELLED/OBSOLETE/SKIPPED |
| `side_effect` | KG `Action.side_effect` | trigger 策略 |
| `interruptible` | KG `Action.interruptible` | 預設 true |
| `deadline_sec` | KG `Action.expected_latency_sec` × 倍率 | timeout |

舊節點完全相容：缺欄位時走預設值（`interruptible=true`、`side_effect="none"`、`deadline_sec=30`）。

---

## 7. 監測迴圈（PRA cycle）

```
on_activate:
  plan = plan_intention(intention)
  if leaf_unresolved: terminate
  result = execute_plan_with_monitoring(plan)
  terminate

execute_plan_with_monitoring(plan):
  cursor = PlanCursor(plan)
  monitor = ExecutionMonitor()
  budget = BudgetGuard(...)
  while not cursor.done() and not budget.exhausted():
    drain monitor → (env_changes, action_results)
    decision = trigger.decide(env_changes, action_results, cursor)
    if decision is ABORT: break
    if decision is non-NONE: repair.apply(decision); continue
    next_atomics = cursor.next_ready_atomics()
    for atom in next_atomics:
      dispatch_async(atom)   # publish + subscribe(return_topic)
    monitor.wait_one(timeout=poll_interval)
  return cursor.summary()
```

---

## 8. 向後相容矩陣

| 情境 | 行為 |
|------|------|
| 舊 IA + 新 Executor | 新 executor 看見無 `task_id` 的 payload，走舊同步路徑，正常回 auto-reply |
| 新 IA + 舊 Executor | 新 IA dispatch 時若偵測不到 `*.result`（timeout），fallback 改走 `publish_sync` 同步呼叫，沿用既有路徑 |
| 監測模組關閉（config 開關） | `execute_plan_with_monitoring` 退回 `execute_plan` |

config 開關：`agent_config.intent.monitoring.enabled`（預設 true）。

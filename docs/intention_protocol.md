# GIAS 意圖管理介面（Intention Protocol）

IntentionalAgent 以 **Intention** 管理使用者的意圖：意圖有生命週期，可跨時間追蹤；
多個意圖依優先序排隊，高優先序可搶占；狀態寫入 Blackboard 並即時發佈。
實作在 `src/core/intention/`，與 IntentionalAgent 的整合在 `src/core/intentional_agent.py`。

---

## 1. 兩種執行方式

| 方式 | 啟動 | 行為 |
|------|------|------|
| 單次（既有啟動腳本） | `IntentionalAgent(config, "帶我去 A12")`、`python -m src.run_intentional_agent "<意圖>"` | 處理完這個意圖就結束；不開放 MQTT 意圖介面 |
| 常駐服務 | `IntentionalAgent(config)`（不給 intention）、`python -m src.run_intentional_agent --serve` | 持續經 MQTT 接收意圖；同一個 broker 上請只執行一個服務 |

程式內也可以直接呼叫 `agent.intentions.submit(text, priority=..., requester=...)`、`cancel(intention_id)`、
`get(intention_id)`、`list()`。

---

## 2. 生命週期

```
PENDING ──► PLANNING ──► EXECUTING ──► ACHIEVED
   │            │  │          │  │
   │            │  │          │  └──► FAILED
   │            │  └──► FAILED│
   │            ▼             ▼
   │        SUSPENDED ◄───────┘      被高優先序意圖搶占；之後重新規劃續做
   │            │
   └────────────┴──► DROPPED        要求者取消

任何未結束的狀態 ──► INTERRUPTED     服務關閉或重啟時尚未完成
```

| 狀態 | 意義 |
|------|------|
| `pending` | 已提出，排隊等待 |
| `planning` | 規劃中（LLM 拆解、比對 Action KG） |
| `executing` | 已承諾計畫，監測迴圈執行中 |
| `suspended` | 被高優先序意圖搶占：執行中的動作已取消，等待重新規劃續做 |
| `achieved` | 所有步驟完成 |
| `failed` | 規劃失敗（例：沒有相關的 action）或執行失敗 |
| `dropped` | 要求者取消 |
| `interrupted` | 服務關閉或重啟時尚未完成（不會自動續做） |

---

## 3. 排程規則

- 一次處理一個意圖：從 `pending` / `suspended` 中挑**優先序最高**者（`priority` 數字越大越優先），同優先序**先到先做**
- **搶占**：新意圖的優先序**高於**正在處理的意圖時，要求目前的意圖停止（取消它執行中的動作），
  它轉為 `suspended`；輪到它時重新規劃，並告訴規劃者哪些步驟已完成、不要重做（以及目前的環境事實）。
  同優先序不搶占。可用 `[intent.intentions] preemption = false` 關閉
- **取消**：排隊中的意圖直接 `dropped`；處理中的意圖要求停止，結束後標為 `dropped`。
  若停止要求送達前意圖已經完成，結果以實際完成為準（`achieved`）

---

## 4. MQTT 介面

| Topic | 方向 | 用途 |
|-------|------|------|
| `intention.submit` | 用戶端 → IA | 提出意圖 |
| `intention.cancel` | 用戶端 → IA | 取消意圖 |
| `intention.query` | 用戶端 → IA | 查詢一個或全部意圖 |
| `intention.status` | IA → 用戶端 | 每次狀態轉換發佈一次 |

`submit` / `cancel` / `query` 以 `publish_sync` 呼叫時會收到回覆（auto-reply）。

**submit**

```json
{"text": "帶我去 AI 展區，避開人潮", "priority": 0, "requester": "kiosk-3", "intention_id": "可選，自訂 id"}
```
回覆：`{"ok": true, "intention_id": "int-…", "status": "pending"}`；`text` 為空或 id 重複時 `{"ok": false, "error": "…"}`

**cancel**

```json
{"intention_id": "int-…", "reason": "使用者離開"}
```
回覆：`{"ok": true, "intention_id": "int-…"}`；已結束或不存在時 `ok` 為 false

**query**

```json
{"intention_id": "int-…"}             // 單一意圖（含 history）
{"include_terminal": false}            // 全部（不含 history）；預設包含已結束的意圖
```

**status**（每次狀態轉換）

```json
{
  "intention_id": "int-…", "text": "帶我去 AI 展區，避開人潮", "priority": 0, "requester": "kiosk-3",
  "status": "executing", "reason": "plan committed", "attempts": 1,
  "created_at": 1760000000.0, "updated_at": 1760000003.2,
  "plan_steps": [{"id": "1", "task": "SuggestRoute", "params": {"destination": "AI_Tech_Area", "avoid_crowded": true}}],
  "result": null
}
```
結束時 `result` 為 `{"ok", "message", "state_counts", "stopped"}`。

---

## 5. 持久化

每次狀態轉換都寫入 Blackboard KG（經 BlackboardAgent 的 `write` 命令，背景寫入，不阻塞意圖處理）：

```
(:Intention {manager, intention_id, text, priority, requester, status, reason, attempts,
             created_at, updated_at, plan_steps, result, history})
```

`plan_steps`、`result`、`history` 為 JSON 字串。常駐服務啟動時，會把同一個 `manager` 尚未結束的意圖標為
`interrupted`（不自動續做）；單次模式不會動到既有記錄。黑板代理不在線時只記一次 warning，意圖處理照常。

---

## 6. 設定（`gias.toml`）

```toml
[intent.intentions]
store = "blackboard"           # blackboard（預設）或 memory
manager_name = "intentional_agent"
preemption = true
```

# GIAS 步驟之間的資料傳遞與最後回覆

一個意圖通常拆成多個步驟。後面的步驟常需要前面步驟的結果（例：「推薦展品」的推薦清單，交給「規劃路線」當途經點），
執行完也要把各步驟的結果整理成一段話回覆使用者。

- 資料傳遞：`src/core/intent/dataflow.py`；planner、監測迴圈、同步執行路徑都會用到
- 最後回覆：`src/core/intention/response.py`；在 IntentionalAgent 處理完意圖時產生

---

## 1. Action 的輸出（Action KG）

```
(:Action)-[:HAS_OUTPUT {order}]->(:Output {key, name, desc, type})
```

seed 以 `write_action_outputs` 寫入（seed 開頭先執行 `CLEAR_ACTION_OUTPUTS`）。執行端把輸出放在結果的 `result` 欄位：

```json
{"task_id": "...", "ok": true, "message": "已根據興趣 ['AI'] 推薦展區。 開放展位: TechCorp AI",
 "result": {"booths": ["B_AI1"], "exhibitors": ["TechCorp AI"], "avoid_zones": []}}
```

`result.adaptations`（執行端的調整記錄，見 monitoring_protocol §5.1）不是輸出。每個步驟另有固定的輸出
`message`（文字回覆）。

目前宣告的輸出：

| Seed | Action | 輸出 | 執行端 |
|------|--------|------|--------|
| 展覽 | RecommendExhibits | `booths`、`exhibitors`、`avoid_zones` | InfoAgent（`limit` 參數限制數量） |
| 展覽、導航 | CrowdStatus | `crowded_zones`、`normal_zones`、`sparse_zones` | InfoAgent |
| 展覽 | LocateExhibit、NavigationAssistance | `current_location`（抵達位置） | NavigationAgent |
| 展覽 | SuggestRoute | `waypoints`（新增同名參數） | NavigationAgent |
| 導航 | LocateExhibit、NavigationAssistance、SuggestRoute | `reached`、`current_location` | GuideAgent |

---

## 2. 規劃：LLM 以 `inputs` 引用前面步驟的輸出

拆解 prompt 的動作清單會列出每個 Action 的輸出：

```
- RecommendExhibits(Interests, CurrentLocation, Limit): 根據訪客興趣推薦適合的展區或活動 | Outputs: booths (list[string]): 推薦的攤位 id，依推薦順序; ...; message: text reply
```

atomic 子意圖需要前面步驟的結果時，不猜值，改寫在 `inputs`，並在 `action` 中省略該參數：

```json
{"sub_intents": [
   {"id": "1", "intent": "推薦 AI 展品", "action": "RecommendExhibits(Interests=[\"AI\"])", "is_atomic": true, "inputs": []},
   {"id": "2", "intent": "規劃參觀路線", "action": "SuggestRoute(CurrentLocation=\"入口\")", "is_atomic": true,
    "inputs": [{"param": "waypoints", "from_id": "1", "output": "booths"}]}
 ],
 "relationships": [{"type": "Sequence", "from_id": "1", "to_id": "2"}]}
```

- `output`：輸出名稱，可加索引或欄位（`booths[0]`、`route.path`），或 `message`
- `from_id`：同一層的子意圖 id；下層拆解時可用 `"@<id>"` 引用前面步驟（prompt 的「Results Available from Earlier Steps」段落會列出：
  已規劃、且不排在這部分之後的兄弟步驟與其子孫）

planner 把它轉成節點上的 `inputs: [{"param", "from", "output"}]`（`from` 為計畫中的節點 id），並：

- 參數名稱依 schema 對應（`Waypoints` → `waypoints`）；不在 schema 內記為 `invalid_input`
- 由 `inputs` 提供的必填參數不算缺少
- atomic 節點附上該 Action 宣告的 `outputs`

### 規劃後驗證

`plan_intention` 在檢查節點 id 與循環之後驗證所有 `inputs`：

| 情況 | 處理 |
|------|------|
| 來源與目標已有先後（Sequence） | 保留 |
| 來源與目標沒有先後 | 在共同的上層 composite 補上 Sequence（資料相依代表先後） |
| 來源排在目標之後、來源不存在、來源是 composite、引用自己、來源沒有宣告這個輸出 | 移除該 input，記為 `invalid_input`；若參數必填，另記 `missing_required` |

`invalid_input` 與其他參數問題一樣依 `[intent] param_validation` 處理：`warn`（預設）記錄後照常執行，`strict` 拒絕計畫。

---

## 3. 執行：派工前代入實際值

監測迴圈中：

1. 節點的 `inputs` 來源還沒結束時不派工（規劃時已排好先後，這是給直接傳入的計畫的保險）
2. 派工前從來源步驟的結果取值，依參數 schema 轉型（只有一個元素的清單給非清單參數時取該元素），放進 params；
   之後才檢查前置條件（A-02），所以前置條件看到的是實際值
3. 來源沒有完成、或沒有產生該輸出（含空字串、空清單）時不派工，以失敗結果交給 trigger：

   ```json
   {"task_id": "...", "ok": false, "error": "input unavailable: step 1 produced no 'booths' (needed for waypoints)"}
   ```

   這類失敗與前置條件不成立一樣**不重試**，直接重規劃子樹

同步執行路徑（`intent.monitoring.enabled = false`）也會代入，取不到時該步驟記為失敗。

### 重規劃脈絡

重規劃時，脈絡多了 `completed_steps`：已完成步驟的參數、文字回覆與輸出。prompt 會列出這些結果，並要求新計畫
不要重做已完成的步驟，需要時直接把結果值寫進參數。被搶占後續做的意圖，已完成步驟也帶有結果。

---

## 4. 最後回覆

IntentionalAgent 處理完一個意圖（完成、部分失敗或無法規劃）後產生回覆；被搶占或取消而中止的意圖不產生。

| 模式 | 行為 |
|------|------|
| `llm`（預設） | 把使用者的請求、整體結果與各步驟結果（依執行順序，不含被重規劃取代的步驟）交給 LLM，要求以使用者的語言、只根據這些結果、最多 5 句回覆。LLM 失敗或回覆為空時改用 `template` |
| `template` | 固定格式：`已完成「…」。`／`「…」未能全部完成。`，再逐行列出各步驟的結果或失敗原因；無法規劃時為 `抱歉，無法處理「…」：<原因>` |
| `off` | 不產生回覆 |

```toml
[intent.response]
mode = "llm"     # llm / template / off
```

回覆放在：

- 執行摘要的 `response`：`{"text", "source"}`（`source` 為 `llm` 或 `template`）
- 意圖的 `result.response`（文字），隨 `intention.status` 發佈並寫入 Blackboard
- 單次模式（啟動時給 intention）結束前印在主控台

直接呼叫 `plan_intention` / `execute_plan_with_monitoring` 的程式（例如導航 benchmark）不會自動產生回覆，
需要時可呼叫 `agent.compose_response(intention, plan=plan, summary=summary)`。

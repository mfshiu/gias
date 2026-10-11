# GIAS Action 前置條件與效果

LLM 提出計畫，KG 驗證：每個 Action 可以在 Action KG 宣告**前置條件**（執行前必須成立的狀態）與
**效果**（執行完成後預期的狀態）。IntentionalAgent 在兩個時間點檢查：

1. **規劃後**：整份計畫依執行順序檢查一次；不成立時帶著違反原因重新規劃一次，仍不成立就拒絕
2. **派工前**：每個步驟派工前再查一次（狀態可能在規劃後改變）；不成立時不派工，直接重規劃該子樹

實作在 `src/core/intent/preconditions.py`，讀寫 KG 在 `src/kg/action_store.py`，
與 IntentionalAgent 的整合在 `src/core/intentional_agent.py`。

---

## 1. KG schema

```
(:Action)-[:REQUIRES]->(:Precondition {key, arg, op, value, desc, severity})
(:Action)-[:PRODUCES]->(:Effect {key, arg, value, value_arg, desc})
```

| 欄位 | 說明 |
|------|------|
| `key` | 狀態變數名稱（例：`zone_state`）。怎麼查由 `DomainProfile.state_probes` 定義，KG 資料本身不含可執行的查詢 |
| `arg` | 用 action 的哪個參數值當作查詢對象（例：`target_name`）；空白表示全域狀態變數 |
| `op` | `eq` / `ne` / `in` / `not_in` / `exists` / `not_exists` |
| `value` | 比較值；`in` / `not_in` 為清單；`exists` / `not_exists` 不需要 |
| `desc` | 一行說明，出現在 log、錯誤訊息與重規劃 prompt |
| `severity` | `block`（預設）：違反就不可執行；`warn`：只記錄 |
| `value_arg`（Effect） | 效果的值取自哪個參數（例：移動後 `visitor_location` 變成 `target_name` 的值）；沒有時用 `value` |

比較規則：

- 一律以字串比較；**查無此狀態**視為值 `None`（例：`not_in ["Closed"]` 成立、`exists` 不成立）
- **無法驗證**（黑板代理不在線、`state_probes` 沒有這個 key、步驟缺少 `arg` 指定的參數）記為 `unverified`，**不算違反**
- 參數值會先經 `DomainProfile.entity_aliases` 對應到實體 id（例：「AI 展區」→ `AI_Tech_Area`）再查詢

---

## 2. 狀態變數（`DomainProfile.state_probes`）

`狀態變數名稱 -> Cypher`。Cypher 以 `$arg` 接收查詢對象，回傳欄位 `value`（取第一列）；
經 `blackboard.control {command: query}` 在 Blackboard KG 上查詢。

場館情境內建 `VENUE_STATE_PROBES`（`src/blackboard/client.py`），導航與展覽 profile 都已使用：

| key | 查詢對象 | 值 |
|-----|----------|----|
| `zone_state` | Zone 名稱，或位於某 Zone 的 POI / Booth id | 該區目前狀態 `Normal` / `Crowded` / `Closed` / `Sparse`；查無區域為 `None` |
| `booth_status` | Booth id | `coalesce(status, 'open')`；不是攤位時為 `None` |

同一次檢查內查詢結果會快取；黑板查詢失敗後 30 秒內不再查詢（期間的前置條件記為 unverified）。

---

## 3. 規劃後檢查

`plan_intention` 在計畫通過白名單、參數與循環檢查後：

1. 依 `PlanCursor` 的執行層級走訪整份計畫。較早層級步驟的**效果**成為後續步驟的預期狀態，
   後續步驟先看預期狀態，找不到才查 Blackboard；同一層（平行）的步驟彼此看不到效果
2. 有 `block` 級違反 → 帶著脈絡重新規劃一次：

   ```json
   {
     "replan": {
       "kind": "precondition",
       "reason": "the previous plan violates action preconditions; choose actions or targets whose preconditions hold",
       "affected_steps": [{"id": "1", "task": "LocateExhibit", "params": {"target_name": "AI_Tech_Area"},
                           "state": "invalid",
                           "error": "precondition failed: 目的地所在區域沒有關閉 (zone_state[AI_Tech_Area]: expected not_in ['Closed'], actual Closed)"}]
     },
     "env_facts": {"zone_states": [...]}
   }
   ```

3. 新計畫重新驗證；仍有 `block` 級違反 → 回傳 `leaf_unresolved`，原因 `Plan violates action preconditions.`
4. 所有檢查結果（含 `warn` 與 `unverified`）記在 `plan["debug"]["precondition_findings"]`

---

## 4. 派工前檢查

監測迴圈派工前，對有前置條件的步驟再檢查一次。已完成步驟（DONE）的效果依完成順序視為目前狀態。

不成立時不派工，以失敗結果交給 trigger：

```json
{"task_id": "...", "ok": false, "error": "precondition failed: <describe>"}
```

錯誤訊息以 `precondition failed` 開頭的失敗**不重試**（重試也不會改變狀態），直接升級為 `REPLAN_SUBTREE`，
重規劃脈絡的 `affected_steps` 帶著這則錯誤。`warn` 級違反只寫 log。

---

## 5. 設定

```toml
[intent.preconditions]
enabled = true                 # false：完全不檢查
check_before_dispatch = true   # false：只在規劃後檢查
```

沒有宣告前置條件的 Action 不受影響；KG 中沒有 `Precondition` / `Effect` 節點時行為與之前相同。

---

## 6. 在 seed 中宣告

在 seed 的 action 定義加上 `preconditions` / `effects`，seed 時以 `write_action_conditions` 寫入
（seed 開頭先執行 `CLEAR_ACTION_CONDITIONS` 清掉舊的節點）：

```python
{
    "name": "LocateExhibit",
    "topic": "navigation.request",
    "task": "LocateExhibit",
    "preconditions": [
        {"key": "zone_state", "arg": "target_name", "op": "not_in", "value": ["Closed"],
         "desc": "目的地所在區域沒有關閉"},
        {"key": "booth_status", "arg": "target_name", "op": "ne", "value": "closed",
         "desc": "目的地攤位開放中"},
    ],
    "effects": [
        {"key": "visitor_location", "value_arg": "target_name", "desc": "訪客到達目的地"},
    ],
    ...
}
```

目前已宣告的前置條件：

| Seed | Action | 前置條件 |
|------|--------|----------|
| `navigation_scenario/seed_actions.py` | LocateExhibit（`target_name`）、NavigationAssistance、SuggestRoute（`destination`） | 目的地區域未關閉、目的地攤位開放（block） |
| `tests/seed_actions_with_embeddings/seed_actions_simple.py` | LocateExhibit（`target_name`）、NavigationAssistance（`destination`） | 同上（block） |
| 同上 | ExplainExhibit（`target_name`） | 攤位開放（warn） |

目前沒有宣告效果（場館的狀態變數都由環境決定）。修改 seed 後須重新執行 seed 才會寫進 Action KG
（注意：seed 會清空並重建 Action KG）。新增狀態變數時，同時在 profile 的 `state_probes` 加上對應的 Cypher。

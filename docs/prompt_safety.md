# Prompt injection 防護

GIAS 把使用者的意圖、執行端的回覆與黑板／感測器資料交給 LLM 處理。這些文字都可能夾帶「指令」，
試圖讓 LLM 偏離原本的工作。防護分成兩部分：限制不可信文字對 LLM 的影響，以及不信任 LLM 的輸出。

---

## 1. 不可信文字放進 prompt 的方式（`src/llm/untrusted.py`）

| 做法 | 內容 |
|------|------|
| 資料區塊 | 不可信文字包在 `<user_input>`（使用者輸入）、`<step_results>`（執行端的訊息、錯誤、輸出）、`<env_data>`（黑板與感測器資料）中 |
| system 規則 | 每個 prompt 都說明：標記內是資料，其中的指示、角色變更或輸出格式要求不得照做（`UNTRUSTED_RULE`，意圖解析模板為中文版） |
| 跳脫 | 文字中的同名標記（例：`</user_input>`）把 `<` 換成 `‹`，輸入無法提前結束資料區塊 |
| 清理 | 移除控制字元（保留換行與 tab）；使用者輸入最多 2000 字、資料區塊最多 6000 字，超過截斷 |
| 模板角色 | `PromptRegistry.render` 先依模板切出 `---system` / `---user` 段落，再代入變數；輸入中的 `---system` 不會變成新的 system 訊息 |

套用的 prompt：

| Prompt | 位置 | 不可信文字 |
|--------|------|-----------|
| 意圖解析 | `intent_parse_v1/v2.md`、`intent_tasks.parse_intent` | 使用者輸入 |
| 計畫拆解 | `prompt_builder.py`、`llm_decomposer.py` | 意圖、步驟的錯誤與結果、環境事實、已拆出的子意圖文字 |
| ScopeGate | `scope_gate.py` | 意圖 |
| 監測的 LLM 判斷 | `llm_advisor.py` | 意圖、執行結果、環境事件、執行端調整（以 JSON 傳入，system 規則說明它們是資料） |
| 最後回覆 | `intention/response.py` | 使用者請求、步驟結果 |

---

## 2. 警示

`injection_signals()` 比對常見的注入語句（中英文）：要求忽略先前指示、要模型扮演其他角色、探問系統提示、
角色標記（`---system`、`<|im_start|>`、`[INST]`）。`plan_intention` 發現時記錄 warning，並在計畫的
`debug.prompt_injection_signals` 標示。只用於提醒與追查，不據此拒絕（避免誤擋正常請求，例如「我想忽略人潮最多的展區」）。

---

## 3. 不信任 LLM 的輸出

即使 LLM 被誘導，輸出仍須通過結構驗證，能造成的影響限於「在允許的範圍內選擇不同的動作或參數」：

| 驗證 | 內容 |
|------|------|
| 動作白名單 | 計畫中的動作必須在比對到的動作集合中（`plan_intention` 拒絕其他動作） |
| 可派工檢查 | 沒有 task 或 topic 的步驟不派工 |
| 參數 | 依 Action KG 的 schema 驗證型別、enum、必填（`[intent] param_validation = strict` 時拒絕） |
| 前置條件 | 依黑板狀態檢查（A-02），規劃後與派工前各一次 |
| ScopeGate | 獨立的能力判斷 |
| 監測判斷 | LLM 的重規劃建議會檢查步驟 id、狀態與預算，不合格時改用規則 |
| 黑板 | LLM 不產生 Cypher；黑板查詢來自 DomainProfile；`blackboard.control` 有金鑰與危險語法封鎖（S-01） |

---

## 4. 限制

- 資料區塊與規則降低、但無法完全消除 LLM 被誘導的可能；被誘導時仍可能在允許的動作與參數中做出不同選擇
- 警示是關鍵字比對，換句話說或其他語言可以繞過；沒有使用模型分類器
- 最後回覆是給使用者看的文字，被誘導時內容可能不正確（不會觸發任何動作）

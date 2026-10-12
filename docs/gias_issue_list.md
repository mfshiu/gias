# GIAS 問題清單

- **建立日期**：2026-10-09
- **分析基準**：`main` @ `51ea57f`，加上工作目錄中尚未 commit 的修改（含 v2 監測子系統 `src/core/monitoring/`）
- **測試現況**：離線單元測試（`tests/monitoring`、`tests/navigation_scenario`、`tests/agents`、`tests/test_execute_plan_with_monitoring.py`）共 548 個，全部通過
- **總數**：43 項（嚴重 5／高 12／中 15／低 11；C-10 為修 C-08 時發現並一併修正）

## 編號與分級規則

編號格式為「類別代碼-序號」，同一類內序號越小越嚴重。

| 代碼 | 類別 |
|---|---|
| V | 實驗驗證 |
| L | 閉環／監測 |
| C | 正確性 |
| A | 架構與論文主張的落差 |
| P | 效能與成本 |
| G | 通用性與耦合 |
| S | 安全 |
| E | 工程品質 |

| 等級 | 定義 |
|---|---|
| 嚴重 | 會讓論文結論失真，或系統產生錯誤結果卻不報錯 |
| 高 | 核心功能沒實作或沒接上，或有卡死、安全風險 |
| 中 | 效能、通用性、設計上的落差，會影響品質，但不會直接出錯 |
| 低 | 工程習慣，或日後擴充時才會碰到的問題 |

狀態欄：`未處理` → `處理中` → `已修正`（附 commit）／`不處理`（附理由）

## 總覽表

| 類別 | 嚴重 | 高 | 中 | 低 |
|---|---|---|---|---|
| **V** 實驗驗證 | V-01, V-02 | V-03, V-04, V-05 | V-06 | — |
| **L** 閉環／監測 | L-01 | L-02, L-03 | L-04 | L-05 |
| **C** 正確性 | C-01, C-02 | C-03, C-04, C-10 | C-05 ~ C-08 | C-09 |
| **A** 架構與論文主張的落差 | — | A-01, A-02 | A-03, A-04 | — |
| **P** 效能與成本 | — | — | P-01 ~ P-03 | P-04, P-05 |
| **G** 通用性與耦合 | — | — | G-01, G-02 | G-03 |
| **S** 安全 | — | S-01 | S-02, S-03 | S-04 |
| **E** 工程品質 | — | E-01 | — | E-02 ~ E-06 |
| **合計** | **5** | **12** | **15** | **11** |

---

## 一、嚴重（5 項）

| 編號 | 類別 | 問題 | 位置 | 影響 | 狀態 |
|---|---|---|---|---|---|
| **V-01** | 實驗驗證 | 導航實驗完全沒呼叫 IntentionalAgent，改用 Dijkstra 模擬，失敗則依 `replan_failure_probability` 隨機注入 | [runner.py:18](../navigation_scenario/runner.py#L18) | TSR 95%、RSR 80.9% 量到的是圖演算法，不是 GIAS 的重規劃 | 已修正（736c1b6；尚未在真實服務上執行） |
| **V-02** | 實驗驗證 | 核心的監測執行路徑 `execute_plan_with_monitoring` 只有 mock 單元測試，沒有任何端到端實驗跑過 | [intentional_agent.py:508](../src/core/intentional_agent.py#L508) | 「監控與重新考慮」這項主張缺乏實驗支持 | 已修正（736c1b6；尚未在真實服務上執行） |
| **L-01** | 閉環／監測 | 黑板環境事件沒有接進監測器：全 repo 沒有地方訂閱 `on_env_event`，`_bb_subs_active` 也從未使用（檔頭註解宣稱有訂閱） | [monitor.py:52](../src/core/monitoring/monitor.py#L52)、[intentional_agent.py:99](../src/core/intentional_agent.py#L99) | 環境改變永遠不會觸發重規劃，系統只對動作失敗反應，閉環沒有成立 | 已修正（3b4495c） |
| **C-01** | 正確性 | 巢狀計畫的節點 ID 衝突：LLM 每層都從 "1" 開始編號，cursor 遇到重複 ID 就靜默略過（已實測重現，見附錄） | [planner.py:145](../src/core/intent/planner.py#L145)、[cursor.py:105](../src/core/monitoring/cursor.py#L105) | 子任務沒被執行，最後卻回報 `ok=True` | 已修正（8b0cbce） |
| **C-02** | 正確性 | 沒有 action 或 topic 的 atomic 節點（`leaf_no_children`、`leaf_forced_atomic`，或 action 為空字串的節點）會繞過白名單檢查，被預設送到 `info.request`，而 InfoAgent 對未知 task 一律回成功 | [planner.py:122](../src/core/intent/planner.py#L122)、[planner.py:139](../src/core/intent/planner.py#L139)、[_executor_utils.py:55](../src/agents/_executor_utils.py#L55)、[info_agent.py:200](../src/agents/info_agent.py#L200) | 根本無法執行的步驟被記為成功（假成功） | 已修正（8b0cbce） |

## 二、高（12 項）

| 編號 | 類別 | 問題 | 位置 | 影響 | 狀態 |
|---|---|---|---|---|---|
| **V-03** | 實驗驗證 | experiment1 只用 `plan_intention()` 產生計畫，執行與重規劃都是 Dijkstra 模擬 | [experiment1/runner.py](../experiment1/runner.py) | 只驗證了規劃，沒驗證執行與調適 | 已修正（1d7d19e；標示為只驗證規劃，由 navigation_scenario live 模式取代） |
| **V-04** | 實驗驗證 | GenExam 是 runner 內另外寫的一條「計畫、檢索、生成、驗證、修正」管線，沒有經過 IntentionalAgent | [genexam_scenario/runner.py](../genexam_scenario/runner.py) | 試題生成的結果不能直接歸功於 GIAS 這個通用框架 | 已修正（ccbcdbd；標示為獨立的出題管線，沒有經過 IntentionalAgent） |
| **V-05** | 實驗驗證 | 論文提到的消融實驗（LLM-only、一般 RAG、拿掉 IPM、拿掉 MAS）在 repo 裡找不到程式 | — | 無法重現；若程式放在別處，請一併納入 | 不處理（repo 中沒有消融實驗的程式；對照實驗只用於驗證，不影響框架本身） |
| **L-02** | 閉環／監測 | 子樹和根層的重規劃都不使用 `env_facts`，等於用同樣的輸入再問一次 LLM | [intentional_agent.py:788](../src/core/intentional_agent.py#L788) | 重規劃很可能得到同樣的計畫 | 已修正（3b4495c） |
| **L-03** | 閉環／監測 | LLM 輔助觸發沒有接上：`enable_llm_assist` 預設為 False，也沒有注入 `llm_decider` | [trigger.py:35](../src/core/monitoring/trigger.py#L35)、[intentional_agent.py:542](../src/core/intentional_agent.py#L542) | REPAIR_NODE 分支永遠走不到 | 已修正（3b4495c） |
| **C-03** | 正確性 | ScopeGate 的 `decide()` 自己吞掉例外並回傳 `can_execute=True`，所以 strict 設定無效 | [scope_gate.py:53](../src/core/intent/scope_gate.py#L53)、[intentional_agent.py:329](../src/core/intentional_agent.py#L329) | `scope_gate_strict = true` 形同虛設，判斷出錯時會直接放行 | 已修正（8b0cbce） |
| **C-04** | 正確性 | 節點沒有逾時機制，`deadline_sec` 預設為 None | [intentional_agent.py:613](../src/core/intentional_agent.py#L613)、[budget.py:20](../src/core/monitoring/budget.py#L20) | 執行端掛掉或訊息遺失時，迴圈會永遠等待 | 已修正（8b0cbce） |
| **C-10** | 正確性 | 環境事件或失敗觸發子樹／整體重規劃時，先取消執行中的步驟才產生新計畫；重規劃失敗時被取消的步驟被標為 OBSOLETE 而無聲丟棄，且 OBSOLETE 不計入成敗，可能回報成功 | [intentional_agent.py:`_apply_decision`](../src/core/intentional_agent.py) | 「先去 A 再去 B」：往 A 的途中重規劃失敗，A 被丟掉、B 完成後仍回報 ok=True | 已修正（934c9c9） |
| **A-01** | 架構落差 | 意圖只是一個字串：沒有 Intention 模型和生命週期狀態，不支援多意圖、優先序或衝突管理，Agent 執行完就結束 | [intentional_agent.py:70](../src/core/intentional_agent.py#L70)、[intentional_agent.py:136](../src/core/intentional_agent.py#L136) | 「跨時間維持意圖」與 Bratman 意圖理論的主張沒有落實 | 已修正（08db68b） |
| **A-02** | 架構落差 | 前置條件驗證沒有實作：Action KG 沒有前置條件和效果，`preconditions_by_action`、`conflicts_between_intents` 已寫好卻從未被呼叫 | [queries.py:211](../src/kg/queries.py#L211)、[queries.py:233](../src/kg/queries.py#L233) | 摘要所說「KG-RAG 提供前置條件驗證」與程式不符；LLM 產生的計畫沒有符號層面的驗證 | 已修正（5355cf0） |
| **S-01** | 安全 | `blackboard.control` 會執行任何 MQTT 用戶端送來的 Cypher，沒有白名單也沒有權限控管 | [blackboard/agent.py:212](../src/blackboard/agent.py#L212)、[blackboard/agent.py:232](../src/blackboard/agent.py#L232) | 任何連得上 broker 的人都能讀取或刪除整個黑板 | 已修正（2a36125） |
| **E-01** | 工程品質 | 核心監測子系統 `src/core/monitoring/` 還沒加入 git 追蹤，另有約 1,100 行修改未 commit | `src/core/monitoring/` | 程式可能遺失，版本也無法追溯 | 已修正（c2ab5bf） |

## 三、中（15 項）

| 編號 | 類別 | 問題 | 位置 | 影響 | 狀態 |
|---|---|---|---|---|---|
| **V-06** | 實驗驗證 | 執行端 Agent 都是模擬的：回傳 `"simulated": True` 和固定訊息 | [_executor_utils.py:158](../src/agents/_executor_utils.py#L158) | 「任務成功」不代表任務真的完成 | 已修正（e31e100；標記改為逐個 task 聲明，並傳到意圖結果與回覆） |
| **L-04** | 閉環／監測 | 動態調適發生在 NavigationAgent 內部（自己查兩次黑板），意圖層並不知道 | [navigation_agent.py:98](../src/agents/navigation_agent.py#L98) | 調適能力歸屬在執行端，不在 IPM，和架構敘述不一致 | 已修正（5cf062a） |
| **C-05** | 正確性 | 有依賴循環時，相關節點會被當成孤兒放到第一層先執行，而且沒有警告 | [cursor.py:191](../src/core/monitoring/cursor.py#L191) | 違反執行順序 | 已修正（059947d） |
| **C-06** | 正確性 | 比對器查不到時把門檻降為 0 重查，參數 gate 全部淘汰時也會關掉 gate 重試 | [action_matcher.py:243](../src/core/intent/action_matcher.py#L243)、[action_matcher.py:343](../src/core/intent/action_matcher.py#L343) | 比對器幾乎不會拒絕，「超出能力範圍」只能靠 ScopeGate 一次 LLM 判斷 | 已修正（059947d） |
| **C-07** | 正確性 | 參數用正規表示式從 LLM 字串解析，不依 `params_schema` 驗證型別、enum 或必填 | [planner.py:23](../src/core/intent/planner.py#L23) | 例如 `Interests=null` 會被當成字串 "null" 傳下去 | 已修正（059947d） |
| **C-08** | 正確性 | 判斷環境事件影響哪些節點時用子字串比對 | [trigger.py:219](../src/core/monitoring/trigger.py#L219) | "A1" 會誤中 "A12"，觸發不必要的重規劃 | 已修正（934c9c9） |
| **A-03** | 架構落差 | 步驟之間不能傳遞資料，也沒有把結果彙整回覆給使用者的步驟 | [planner.py:161](../src/core/intent/planner.py#L161) | 多步驟任務無法串接，例如「推薦展品」的結果傳不到「規劃路線」 | 已修正（f98b410） |
| **A-04** | 架構落差 | 同一個意圖被 LLM 拆兩次：`parse_intent` 的結果只用來比對動作，planner 又重新拆一次 | [intentional_agent.py:226](../src/core/intentional_agent.py#L226)、[intentional_agent.py:250](../src/core/intentional_agent.py#L250) | 兩次結果可能不一致，成本也加倍 | 已修正（33ffb33） |
| **P-01** | 效能 | `select_actions` 不帶 slots 又比對一次，而且每個動作都再查一次 KG（N+1 查詢） | [action_selector.py:59](../src/core/intent/action_selector.py#L59)、[action_selector.py:32](../src/core/intent/action_selector.py#L32) | 規劃延遲變長，embedding 費用增加 | 已修正（7edc41f） |
| **P-02** | 效能 | 每次比對都執行 `SHOW INDEXES` 加 `awaitIndex`；讀不到維度時還會 DROP 索引再重建 | [action_store.py:93](../src/kg/action_store.py#L93)、[action_matcher.py:235](../src/core/intent/action_matcher.py#L235) | 多了往返延遲，正式環境中索引可能被反覆重建 | 已修正（814d66b） |
| **P-03** | 效能 | 黑板每 2 秒輪詢一次，做全量比對 | [watcher.py:49](../src/blackboard/watcher.py#L49) | 有延遲，資料量大時無法擴充 | 已修正（0dbeb5e） |
| **G-01** | 通用性 | 只寫死了 info、navigation 兩個頻道，未知 topic 一律送往 info | [_executor_utils.py:31](../src/agents/_executor_utils.py#L31) | 新增一種執行端 Agent 就要改核心程式 | 已修正（4e5ea49） |
| **G-02** | 通用性 | 觸發條件只看 `Zone/`、`Booth/` 開頭的主題 | [trigger.py:38](../src/core/monitoring/trigger.py#L38) | 綁死展場領域，「通用」的主張打折 | 已修正（ca497df） |
| **S-02** | 安全 | MQTT 用明文帳密，沒有 TLS 或 ACL | `gias.toml` | 訊息可被竊聽或偽造 | 已修正（c11bdc6；AgentFlow 端未 commit） |
| **S-03** | 安全 | OpenAI API 金鑰以明文存在 `gias.toml`（已在 .gitignore、沒被 git 追蹤） | `gias.toml` | 本機或備份檔（`_bak/*.7z`）外流時金鑰會一起外洩 | 已修正（f7f2251） |

## 四、低（11 項）

| 編號 | 類別 | 問題 | 位置 | 狀態 |
|---|---|---|---|---|
| **L-05** | 閉環／監測 | 進度訊息只寫進 log，沒有用在監測判斷 | [monitor.py:84](../src/core/monitoring/monitor.py#L84) | 已修正（165b73b） |
| **C-09** | 正確性 | `BudgetGuard.exhausted()` 只看 deadline，和它的註解、文件描述不符 | [budget.py:73](../src/core/monitoring/budget.py#L73) | 已修正（9749343） |
| **P-04** | 效能 | 沒有 embedding 快取（`llm/cache.py` 已存在但沒被使用） | [cache.py](../src/llm/cache.py) | 已修正（877f6be） |
| **P-05** | 效能 | Planner 遞迴時固定 `sleep(0.1)`，子意圖逐一處理、沒有平行化 | [planner.py:183](../src/core/intent/planner.py#L183) | 已修正（0037a5c） |
| **G-03** | 通用性 | BlackboardAgent 裡寫死了展場專用的寫入邏輯（Zone/Booth） | [blackboard/agent.py:246](../src/blackboard/agent.py#L246) | 已修正（450b8ee） |
| **S-04** | 安全 | 使用者輸入直接嵌入 prompt，沒有 prompt injection 防護 | [prompt_builder.py:12](../src/core/intent/prompt_builder.py#L12) | 已修正（b01fdf7） |
| **E-02** | 工程品質 | 用 `_bak/*.7z` 備份取代 git 分支；commit 訊息有 ".."、"aaa" 這類不具描述性的寫法 | `_bak/` | 已修正（a873e97） |
| **E-03** | 工程品質 | 有空殼或沒被使用的模組：`llm/cache`、`router`、`policy`、`observability`，`schemas/plan.py`、`eval.py`，`tasks/planning_tasks.py`、`evaluation_tasks.py`，`kg/client.py` | `src/llm/`、`src/kg/` | 已修正（379b75d） |
| **E-04** | 工程品質 | `print` 和 logger 混用，log 層級無法統一控制 | `planner.py`、`src/agents/*` | 已修正（8aa32f6） |
| **E-05** | 工程品質 | OpenAI 聊天模型寫死成 `gpt-4.1-mini`，無法從 toml 設定 | [openai_provider.py:41](../src/llm/providers/openai_provider.py#L41) | 已修正（2be2b52） |
| **E-06** | 工程品質 | 沒有 CI（`.github/` 是空的），548 個離線測試沒有自動執行 | `.github/` | 已修正（e351ef5） |

---

## 建議處理順序

1. **先修小而明確的 bug：** C-01、C-02、C-03、C-04，每項都能在一個檔案內修好並補回歸測試。
2. **接通閉環：** L-01 → L-02 → L-03。
3. **補實驗：** 先做 V-01、V-02（導航實驗加上真的跑 GIAS 的 `--live` 模式），再補 V-05 的消融實驗程式。（V-05 後來決定不處理，見修正紀錄）
4. **補論文核心機制：** A-01、A-02。
5. **安全與工程：** S-01、E-01 盡早處理，其餘可以慢慢清。

---

## 修正紀錄

### 2026-10-09：C-01 ～ C-04（commit 8b0cbce）

| 編號 | 修正內容 | 主要檔案 |
|---|---|---|
| C-01 | Planner 把 LLM 的同層 id 轉成階層式全域 id（父 "2" 的子節點 "1" → "2.1"），relationships 同步改寫；同層重複或缺 id 時自動補成唯一。PlanCursor 遇到重複 id 改為拋出 `DuplicateNodeIdError`（`replace_subtree` 回傳 False、`replace_root` 拋錯，且都不改動 cursor）；`plan_intention` 與 `execute_plan_with_monitoring` 會拒絕 id 重複的 plan | `planner.py`、`cursor.py`、`repair.py`、`intentional_agent.py` |
| C-02 | `plan_intention` 新增規劃驗證：沒有 task 的 atomic、沒有子節點的 composite 一律判為無法執行（`leaf_unresolved`，原因列在 `debug.unexecutable_nodes`）。兩條執行路徑派工前再檢查一次，不送出、直接標為失敗。InfoAgent／NavigationAgent 收到缺少 task 的請求改回 `ok=False` | `intentional_agent.py`、`_executor_utils.py`、`info_agent.py`、`navigation_agent.py` |
| C-03 | ScopeGate 在 LLM 失敗或回覆格式不符時拋出 `ScopeGateError`，交由 `scope_gate_strict` 決定拒絕或放行；`can_execute` 改為嚴格解析（原本 `bool("false")` 會變成 True） | `scope_gate.py` |
| C-04 | 新增節點逾時：`Budget.node_timeout_sec`（預設 30 秒，設定鍵 `intent.monitoring.node_timeout_sec`，≤ 0 表示不限），節點自身的 `deadline_sec` 優先。逾時節點會送 cancel，並當成失敗結果交給 trigger 走 retry / replan。順帶修正：retry 前舊派工遲到的結果原本會覆寫新一次派工的狀態，現在會被忽略 | `budget.py`、`cursor.py`、`intentional_agent.py`、`docs/monitoring_protocol.md` |

新增回歸測試 37 個（`tests/intent/`、`tests/monitoring/test_cursor.py`、`tests/test_execute_plan_with_monitoring.py`、`tests/test_execute_plan.py`、`tests/agents/`）；離線測試共 606 個全數通過。

另外，`tests/test_plan_intention_policy.py` 原本用「沒有任何步驟的空計畫」當作規劃成功的案例，C-02 修正後會被正確判為無法執行，因此把該測試的假計畫改成含一個可執行步驟。

### 2026-10-09：L-01 ～ L-03（commit 3b4495c）

| 編號 | 修正內容 | 主要檔案 |
|---|---|---|
| L-01 | 執行計畫時，IntentionalAgent 訂閱 `blackboard.subscriber.<agent_id>`（事件轉入 ExecutionMonitor），並對 `DomainProfile.env_subscriptions` 的每個 pattern 向黑板代理送 `subscribe`；結束時（finally）送 `unsubscribe`。黑板代理沒回應時只記 warning，不影響執行。trigger 改為也從 metadata 的 `node_id`／`source_id` 取區域名稱，才對得上 BlackboardWatcher 的關係事件（`Zone/CURRENT_STATE/State`）。`EXPO_PROFILE` 已設定監看 `Zone/*/*`、`Booth/*/*` | `intentional_agent.py`、`domain_profile.py`、`blackboard/client.py`、`blackboard/agent.py`、`trigger.py`、`run_intentional_agent.py` |
| L-02 | PlanRepair 重規劃時整理「重規劃脈絡」：原因、受影響步驟（task、參數、錯誤）、以及依 `DomainProfile.env_fact_queries` 經黑板查到的環境事實，傳給 planner；`plan_intention` → RecursivePlanner → LLMDecomposer → PromptBuilder 一路帶入，prompt 多出 Replanning Context 段落與兩條規則（避開異常地點、不要重複失敗的步驟）。一般規劃的 prompt 不變。`EXPO_PROFILE` 已設定查詢擁擠／封閉區域、關閉攤位、不可用設施、封鎖通道 | `repair.py`、`intentional_agent.py`、`planner.py`、`llm_decomposer.py`、`prompt_builder.py`、`domain_profile.py` |
| L-03 | 新增 `LLMReplanAdvisor`（預設注入 trigger），由 `intent.monitoring.enable_llm_assist` 控制（預設 false）。節點失敗時先問 LLM（可選 REPAIR_NODE 改參數重派），規則對應不到的相關環境變動也交給 LLM；LLM 回覆會驗證節點、狀態與預算，不合格或失敗時回到規則；每個 plan 最多詢問 `llm_max_calls` 次。順帶修正兩個讓 REPAIR／RETRY 實際無法生效的問題：對 IN_FLIGHT 節點的 RETRY／REPAIR 原本會把節點標為 OBSOLETE 而無法重派，現在標為 CANCELLED 後重派；尚未派工的節點可直接改參數（不消耗 retry 配額） | `llm_advisor.py`（新）、`trigger.py`、`repair.py`、`cursor.py`、`intentional_agent.py` |

新增回歸測試 35 個（`tests/monitoring/test_llm_advisor.py`、`tests/intent/test_prompt_context.py`、`test_trigger.py`、`test_repair.py`、`test_cursor.py`、`tests/test_execute_plan_with_monitoring.py`），並以突變測試確認關鍵測試在還原修正後會失敗。離線測試共 643 個全數通過；加上 `tests/blackboard`、`tests/observer`、`tests/test_intentional_agent.py` 的非 integration 測試共 693 個通過、10 個略過。

**尚未處理（與本批相關）**：
- `tests/test_log_colors.py::test_log_colors_visual` 失敗，與本次修改無關（只涉及 `log_helper`）。
- BlackboardWatcher 不監看關係屬性，因此 `CONNECTED_TO.blocked`（通道封鎖）不會產生事件；目前只能在重規劃時透過 `env_fact_queries` 取得。
- 節點與環境事件的對應仍是子字串比對（C-08），區域 ID 與中文名稱寫法不同時需靠 LLM 輔助判斷。


### 2026-10-09：V-01、V-02（commit 736c1b6）

| 編號 | 修正內容 | 主要檔案 |
|---|---|---|
| V-01 | 導航實驗新增 live 模式（`batch_runner --live`、`python -m navigation_scenario.live.runner <case>`）：每個案例都經過 IntentionalAgent 的 LLM 規劃與監測迴圈，由新的 `GuideAgent` 在 Blackboard 圖上實際移動（每步重讀圖、重算路徑，封鎖通道與封閉區不可通行，走不到時回報失敗），事件在機器人走到注入時機時直接寫進 Blackboard，指標依機器人實際軌跡計算。報告標示 Mode（simulation / live），模擬模式行為不變 | `navigation_scenario/live/`（新）、`batch_runner.py`、`analyze.py`、`config.py`、`pathing.py` |
| V-02 | live 模式本身就是 `execute_plan_with_monitoring` 的端到端驗證；另加封閉式端到端測試（真實監測迴圈、trigger、repair、GuideAgent、evaluation，只把 MQTT／Neo4j／LLM 換成記憶體替身），涵蓋四種路徑：環境事件觸發意圖層重規劃、通道封鎖由執行層繞行、執行失敗 → 重派 → 帶錯誤訊息重規劃、案例逾時要求停止。為了讓逾時的案例不會繼續派工干擾下一個案例，IntentionalAgent 新增 `request_stop()` | `tests/navigation_scenario/test_live_*.py`（新）、`intentional_agent.py`、`navigation_agent.py`（新增可覆寫的 `execute_task`） |

新增測試 34 個（GuideAgent 23、指標計算 6、端到端 5），端到端測試連續重跑 5 次皆通過，並以突變測試確認關鍵測試在還原機制後會失敗。全部非 integration 測試共 727 個通過、10 個略過。

**尚未完成**：本機 Neo4j 未啟動，live 模式還沒有在真實的 MQTT + Neo4j + LLM 上跑過；第一次執行前需先用 `navigation_scenario.seed_actions` 建立導航用 Action KG（會清空 actions database，與 `experiment1`、展場 seed 互相覆寫）。現有 `navigation_scenario_results/` 裡的數據仍是模擬模式的結果。

**順帶發現（未修改）**：
- `single_10_*`（「請帶我去最近的洗手間」）預期目標是 `P_Restroom_N`，但從入口出發最近的是 `P_Restroom_S`（52 m vs 72 m）；live 模式中機器人若正確走到最近的洗手間會被判為失敗。
- `analyze._load_case_metrics` 讀檔時沒有載入 `segment_baselines` / `segment_actuals`，報告裡的 PE 實際上是總距離比，而不是 `metrics.py` 定義的逐段平均。
- V-06 部分改善：live 模式的 GuideAgent 依 Blackboard 實際計算路線；預設的 NavigationAgent / InfoAgent 仍是固定訊息的模擬。


### 2026-10-09：C-08、C-10（commit 934c9c9）

| 編號 | 修正內容 | 主要檔案 |
|---|---|---|
| C-08 | 新增 `EntityMatcher`，取代子字串比對：名稱以英數字開頭／結尾時，前後不可緊接英數字或底線（`A1` 不再命中 `A12`，`B_A1` 不再命中 `B_A12`；中文緊鄰時仍可命中），大小寫不敏感，名稱中的空白可對應零個空白。另外支援 `DomainProfile.entity_aliases`（例：`AI_Tech_Area` ↔「AI 展區」）與 `entity_parents`（展區事件也影響前往區內攤位的步驟，反向不成立）；比對時忽略步驟的出發點（`current_location`），關係事件 topic 中段的關係名稱不再當成實體。`NAVIGATION_PROFILE` 依 config 自動產生別名與從屬（不含「入口」這類幾乎都是出發點的別名），`EXPO_PROFILE` 依展場 Blackboard 填入 | `entity_match.py`（新）、`trigger.py`、`domain_profile.py`、`intentional_agent.py`、`navigation_scenario/config.py`、`run_intentional_agent.py` |
| C-10 | 子樹／整體重規劃改為「先產生並換上新計畫，成功後才取消被取代的派工」；重規劃失敗時，原本的步驟繼續執行。RETRY／REPAIR 仍先取消再重派（同一節點） | `intentional_agent.py` |

比對變準後（別名、從屬），環境事件觸發的重規劃會變多，C-10 若不修會讓更多步驟被無聲丟棄，因此一併處理。

新增測試 26 個（比對規則 15、trigger 5、決策套用順序與設定 3、導航端到端 3），並以突變測試確認關鍵測試在還原修正後會失敗。全部非 integration 測試共 753 個通過、10 個略過。


### 2026-10-10：C-05 ～ C-07（commit 059947d）

| 編號 | 修正內容 | 主要檔案 |
|---|---|---|
| C-05 | 遇到依賴循環時，一次只放行循環中在 sub_plans 裡最早出現的一個節點，其他依賴（包括依賴循環的下游節點）照常遵守；原本整個循環連同下游一起被當成孤兒放到第一層。新增 `find_dependency_cycles`，`plan_intention` 拒絕有循環的計畫（`debug.dependency_cycles`），直接傳入執行時記 warning | `cursor.py`、`intentional_agent.py` |
| C-06 | 比對器只做一次向量查詢、在本機依門檻過濾：分數 ≥ `min_score`（預設 0.75）為候選，沒有時退而接受 ≥ `fallback_min_score`（預設 0.6，約 cos 0.2），仍沒有就回傳空串列（子意圖沒有語意相關的 action → 計畫無法完成），不再把門檻降為 0。兩個門檻可由 `[intent.matching]` 設定。參數 gate 全部淘汰時仍回傳不經 gate 的結果（LLM 抽出的 slot 常不完整），但在 evidence 標記 `param_gate_bypassed`，且不再重新呼叫 embedding 與查詢。新增 `python -m src.tools.calibrate_action_matcher` 校準門檻 | `action_matcher.py`、`intentional_agent.py`、`src/tools/calibrate_action_matcher.py`（新） |
| C-07 | 新增 `action_params.py`：只在最外層逗號切分（引號、括號內不切），值轉成 Python 型別（`null` → 不傳、`true/false`、整數 / 小數、清單；有前導零的編號保留字串）；依 `params_schema` 對應參數名稱（不分大小寫與底線，修正 `BoothID` 被轉成 `booth_i_d`）、轉型別、以 `DomainProfile.enum_alias` 檢查 enum、檢查必填，問題記在節點的 `param_issues`。`[intent] param_validation` 預設 `warn`（記錄在 `debug.param_issues` 並照常執行），設為 `strict` 時有缺必填／型別錯／enum 錯就拒絕計畫 | `action_params.py`（新）、`planner.py`、`intentional_agent.py` |

新增測試 27 個（參數解析 11、比對門檻 6、依賴循環 5、規劃驗證與設定 5），並以突變測試確認關鍵測試在還原修正後會失敗。全部非 integration 測試共 780 個通過、10 個略過。

**注意**：
- C-06 的 `fallback_min_score = 0.6` 未經實測校準：gias.toml 的 OpenAI 金鑰目前無效（401 invalid_api_key），無法計算 embedding。金鑰更新後請執行校準工具確認；若場內意圖被誤拒，可調低此值。
- C-07 預設 `warn` 而非 `strict`：展場的旗艦範例「推薦幾個適合我的展區」沒有提供 `RecommendExhibits` 必填的 `interests`，`strict` 會讓它無法規劃。


### 2026-10-10：C-09（commit 9749343）

`BudgetGuard.exhausted()` 是監測迴圈的結束條件，行為（只看 deadline）是對的，錯的是註解（寫成「replan 配額用完 + deadline 過了」）。若照註解把 replan 配額用完也算耗盡，最後一次 replan 一用掉配額迴圈就結束，剛換上的新步驟永遠不會執行（以突變測試確認：`max_replans=1` 的整合測試會失敗）。已修正註解，說明配額用完只代表不能再 replan / retry；並新增單元測試固定此語意（`tests/monitoring/test_budget.py`）。程式行為不變。


### 2026-10-11：L-04（commit 5cf062a）

局部調整留在執行端（Holonic 的分工），但必須回報上層：

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 回報協定 | 執行端以 `*.progress` 的 `state="adapted"` 即時回報調整，並附在結果的 `result.adaptations`；種類有 `reroute`（改路線、目標不變）與 `constraint_relaxed`（沒有路線能滿足限制，只好放寬） | `_executor_utils.py`（`AdaptationLog`）、`docs/monitoring_protocol.md` §5.1 |
| 執行端 | NavigationAgent：兩次查黑板之間人潮改變而改路線時回報 `reroute`。GuideAgent：就地繞行時回報 `reroute`（含原路線與新路線），必須放寬 `avoid_crowded` 時回報 `constraint_relaxed`（連續放寬只回報一次） | `navigation_agent.py`、`navigation_scenario/live/guide_agent.py` |
| 意圖層 | IntentionalAgent 訂閱 progress；ExecutionMonitor 把 `adapted` 事件放進 queue（其他 progress 仍只記 log）；調整記在節點上並出現在執行摘要。`constraint_relaxed` 交給 trigger：啟用 LLM 輔助時由 LLM 判斷是否重規劃（例如原意圖要求避開人潮），否則接受；`reroute` 只記錄 | `monitor.py`、`cursor.py`、`trigger.py`、`llm_advisor.py`、`intentional_agent.py` |
| live benchmark | 每個案例的 `metrics.extra.executor_adaptations_reported` 列出意圖層收到的調整 | `navigation_scenario/live/evaluation.py` |

新增測試 15 個，並以突變測試確認關鍵測試在還原修正後會失敗。全部非 integration 測試共 796 個通過、10 個略過。

L-05（進度訊息只寫進 log）部分改善：`adapted` 事件已用於監測判斷；`running` 等其他進度仍只記 log。


### 2026-10-11：L-05（commit 165b73b）

L-04 已讓 `adapted` 進度事件用於監測判斷；這次讓其他進度事件也發揮作用：

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 心跳 | ExecutionMonitor 把一般進度放進 queue；IntentionalAgent 記到節點上並更新「最後回報時間」。沒有自訂 `deadline_sec` 的節點，逾時改為從最後一次回報起算（閒置逾時），長時間執行但持續回報進度的任務不會被 C-04 的逾時誤判；節點自訂 `deadline_sec` 仍從派工起算。執行端若不送進度，行為與之前相同 | `monitor.py`、`cursor.py`、`events.py`（`ProgressUpdate`）、`intentional_agent.py`、`budget.py` |
| 狀態 | 最新進度（state / ratio / note）出現在執行摘要，並提供給 LLM 判斷時參考 | `cursor.py`、`llm_advisor.py` |
| 執行端 | GuideAgent 每走一步回報進度（目前位置、完成比例），兼作心跳 | `navigation_scenario/live/guide_agent.py` |

新增測試 7 個，並以突變測試確認關鍵測試在還原修正後會失敗（包括「進度不可延長節點自訂截止時間」）。全部非 integration 測試共 803 個通過、10 個略過。


### 2026-10-11：A-01（commit 08db68b）

依確認的方向實作：依優先序排隊並可搶占、寫入 Blackboard 但不自動恢復、MQTT 與 Python API 兩種介面。完整介面見 [`docs/intention_protocol.md`](intention_protocol.md)。

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 意圖模型 | `Intention`：id、文字、優先序、要求者、狀態、原因、嘗試次數、計畫步驟、結果、歷程。生命週期 pending → planning → executing → achieved / failed，可 suspended（被搶占）、dropped（取消）、interrupted（關閉或重啟），不合法的轉換會拋錯 | `src/core/intention/model.py`（新） |
| 排程 | `IntentionManager`：一次處理一個意圖，挑優先序最高者（同優先序先到先做）；新意圖優先序較高時搶占目前的意圖（取消其執行中的動作，轉 suspended，之後重新規劃續做）；取消；每次狀態轉換寫入 store 並通知 | `src/core/intention/manager.py`（新） |
| 持久化 | `BlackboardIntentionStore`：寫成 Blackboard 的 `(:Intention {manager, intention_id, …})`，背景寫入不阻塞；常駐服務啟動時把上次未完成的意圖標為 interrupted。也可用記憶體 store | `src/core/intention/store.py`（新）、`blackboard/client.py`（`write_blackboard`） |
| IntentionalAgent | 建構時給 intention：處理完就結束（既有啟動腳本行為不變）；不給：常駐服務，訂閱 `intention.submit` / `cancel` / `query`，狀態發佈到 `intention.status`。`execute_plan_with_monitoring` 新增 `stop_event`（搶占、取消時停止並取消執行中的動作）。被搶占後續做時，重規劃脈絡會列出已完成的步驟（不重做）與目前環境事實。`python -m src.run_intentional_agent --serve` 啟動服務 | `intentional_agent.py`、`run_intentional_agent.py` |

新增測試 29 個（模型與持久化 10、排程 12、整合 7），多執行緒測試連續重跑 3 次皆通過，並以突變測試確認關鍵測試在還原後會失敗。全部非 integration 測試共 832 個通過、10 個略過。

**仍未涵蓋**：
- 多個意圖同時執行（目前一次一個；同時執行需要資源仲裁，見 A-02 與後續項目）
- 意圖之間的語意衝突偵測（例：兩個意圖目標互相矛盾）；目前靠優先序與搶占處理先後，`conflicts_between_intents` 仍未使用
- 重啟後自動續做（依決定只標為 interrupted）

### 2026-10-11：A-02（commit 5355cf0）

以「LLM 提出計畫、KG 驗證」實作：Action KG 宣告前置條件與效果，計畫在規劃後與派工前各檢查一次。完整說明見 [`docs/action_preconditions.md`](action_preconditions.md)。

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| KG schema | `(:Action)-[:REQUIRES]->(:Precondition {key, arg, op, value, desc, severity})`、`(:Action)-[:PRODUCES]->(:Effect {key, arg, value, value_arg, desc})`。`key` 是狀態變數，查詢方式由 `DomainProfile.state_probes`（Cypher）定義；運算子 eq / ne / in / not_in / exists / not_exists；severity 為 block 或 warn。讀取 `ActionStore.get_action_conditions`，seed 用 `write_action_conditions` | `src/kg/action_store.py`、`domain_profile.py`、`blackboard/client.py`（`VENUE_STATE_PROBES`：`zone_state`、`booth_status`） |
| 檢查邏輯 | 依執行層級走訪計畫，較早步驟的效果成為後續步驟的預期狀態，找不到才查 Blackboard；查無狀態視為 None；黑板無法查詢或缺少參數記為 unverified（不阻擋）；參數值先經 `entity_aliases` 對應到實體 id | `src/core/intent/preconditions.py`（新） |
| 規劃後 | planner 把前置條件與效果附到 atomic 節點。有 block 級違反時帶著違反原因（`replan.kind = "precondition"`）與環境事實重新規劃一次，仍違反就回傳 `leaf_unresolved`（`Plan violates action preconditions.`）；檢查結果記在 `plan.debug.precondition_findings` | `planner.py`、`intentional_agent.py` |
| 派工前 | 再檢查一次（已完成步驟的效果視為目前狀態）；不成立時不派工，以 `precondition failed: ...` 失敗交給 trigger，trigger 不重試、直接重規劃子樹 | `intentional_agent.py`、`events.py`、`trigger.py` |
| 設定 | `[intent.preconditions] enabled`、`check_before_dispatch`（皆預設 true） | `intentional_agent.py` |
| Seed | 導航 LocateExhibit / NavigationAssistance / SuggestRoute 與展覽 LocateExhibit / NavigationAssistance：目的地區域未關閉、攤位開放（block）；展覽 ExplainExhibit：攤位開放（warn） | `navigation_scenario/seed_actions.py`、`tests/seed_actions_with_embeddings/seed_actions_simple.py` |

`queries.preconditions_by_action` 的 schema 說明已更新為上述格式（仍供 `KGClient.check_preconditions` 使用）；IntentionalAgent 改經 ActionStore 一併讀取前置條件與效果。

新增測試 27 個（檢查邏輯與 KG 讀寫 19、規劃 5、監測迴圈與 trigger 3），並以突變測試確認關鍵測試在還原後會失敗（不做修正重規劃、不做派工前檢查、前置條件失敗仍重試、忽略計畫中的效果、派工時忽略已完成步驟的效果、planner 未附前置條件）。全部非 integration 測試（不含 `tests/kg`）共 867 個通過、10 個略過。另有兩項在修正前即存在、與本項無關的問題：`tests/kg` 的 17 個錯誤（patch 目標 `kg.client`、`kg.adapter_neo4j` 不存在）、`test_log_colors_visual` 1 個失敗（log_helper）。

**注意與仍未涵蓋**：
- seed 須重新執行才會把前置條件寫進 Action KG（seed 會清空並重建 Action KG）；尚未在真實的 Neo4j / Blackboard 上執行
- 支援效果，但 seed 目前沒有宣告（場館狀態都由環境決定）
- 意圖之間的衝突偵測（`conflicts_between_intents`）仍未使用，留給多意圖同時執行時處理（見 A-01）

### 2026-10-11：A-03（commit f98b410）

步驟之間的資料傳遞採「LLM 宣告引用、程式代入實際值」：Action KG 宣告每個 Action 的輸出，LLM 拆解時以 `inputs` 把參數接到前面步驟的輸出，派工前才代入實際值；意圖結束時再把各步驟的結果彙整成回覆。完整說明見 [`docs/step_dataflow.md`](step_dataflow.md)。

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 輸出宣告 | `(:Action)-[:HAS_OUTPUT]->(:Output {key, name, desc, type})`；讀取 `ActionStore.get_action_outputs`，seed 用 `write_action_outputs`。拆解 prompt 的動作清單列出輸出 | `src/kg/action_store.py`、`action_selector.py` |
| 規劃 | 子意圖新增 `inputs: [{param, from_id, output}]`（`output` 可加索引，如 `booths[0]`；`message` 為文字回覆）。planner 轉成節點的 `inputs`（id 轉成計畫中的節點 id）、略過 action 字串中的佔位值、由 inputs 提供的必填參數不算缺少；下層拆解時列出前面步驟的輸出，以 `"@<id>"` 引用 | `planner.py`、`prompt_builder.py`、`action_params.py` |
| 驗證 | 來源與目標沒有先後時補上 Sequence；來源不存在、排在後面、沒有宣告該輸出等記為 `invalid_input`（`strict` 時拒絕計畫） | `src/core/intent/dataflow.py`（新）、`intentional_agent.py` |
| 執行 | 來源未結束時不派工；派工前取值並依 schema 轉型，再檢查前置條件；取不到時以 `input unavailable: ...` 失敗，不重試、直接重規劃子樹。同步執行路徑也會代入。重規劃脈絡新增 `completed_steps`（已完成步驟的回覆與輸出），新計畫可直接使用 | `intentional_agent.py`、`cursor.py`、`events.py`、`trigger.py`、`repair.py` |
| 執行端 | 輸出寫在結果的 `result`：RecommendExhibits（`booths`、`exhibitors`、`avoid_zones`，`limit` 生效）、CrowdStatus（各狀態的區域）、導航類（抵達位置；GuideAgent 另有 `reached`）、SuggestRoute（`waypoints`） | `info_agent.py`、`navigation_agent.py`、`navigation_scenario/live/guide_agent.py` |
| 最後回覆 | `ResponseComposer`：`[intent.response] mode = llm`（預設，失敗時改用 template）/ `template` / `off`。回覆放在執行摘要的 `response` 與意圖的 `result.response`（隨 `intention.status` 發佈並寫入 Blackboard），單次模式結束前印出 | `src/core/intention/response.py`（新）、`manager.py`、`intentional_agent.py` |
| Seed | 宣告上述輸出；展覽情境的 SuggestRoute 新增 `waypoints` 參數 | 兩個 seed 檔 |

新增測試 47 個（資料傳遞與回覆單元測試 40、監測迴圈與同步執行 3、規劃驗證 2、意圖服務 2），並以突變測試確認 14 個關鍵行為在還原後會被測試抓到。為配合新行為修改 3 個既有測試：拆解 prompt 多了第 8 條規則（重規劃規則改為 9、10）、導航結果多了 `current_location` 輸出、意圖服務測試改用 template 回覆（避免單元測試呼叫真實 LLM）。全部非 integration 測試（不含 `tests/kg`）共 914 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

**注意與仍未涵蓋**：
- seed 須重新執行才會把輸出寫進 Action KG；尚未在真實的 LLM / Neo4j 上執行，LLM 是否確實使用 `inputs` 還需實測
- 預設模式每個意圖多一次 LLM 呼叫（產生回覆）；導航 benchmark 直接呼叫規劃與執行，不受影響
- `inputs` 只能引用同一份計畫中的步驟；重規劃後的新計畫改以脈絡中已完成步驟的結果值帶入
- 被搶占後續做的意圖，回覆只涵蓋續做這一段的步驟

### 2026-10-11：A-04（commit 33ffb33）

依確認的方向：以意圖解析（`parse_intent`）的拆解為準，planner 根層不再自行重拆。

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 第一層沿用解析結果 | 意圖解析拆出兩個以上子意圖時，這些子意圖（附上抽出的實體與各自比對到的動作）直接成為計畫第一層；拆解 prompt 要求 LLM 保留相同的 id 與順序，只決定是否為原子動作、綁定動作與參數、`inputs` 與先後。只有一個子意圖、或解析失敗以整句代替時不限制，planner 照常拆解 | `intentional_agent.py`（`_given_sub_intents`）、`planner.py`、`prompt_builder.py` |
| 不一致時的處理 | LLM 的子意圖 id 與給定的不同時，帶著差異（缺少與多出的 id）重問一次；仍不同就以給定的為準：保留對應的項目、補回缺少的子意圖（交給下一層拆解）、移除多出的項目與指向它們的關係，記在 `plan.debug.sub_intent_alignment` | `planner.py`（`_align_to_given`） |
| 比對只算一次 | 原本 selector 會對每個子意圖再比對一次（而且不帶 slots，結果可能與第一次不同）；改為直接使用第一次的比對結果 | `action_selector.py`、`intentional_agent.py` |

LLM 呼叫次數不變（解析、ScopeGate、根層拆解各一次），但比對與規劃使用同一組子意圖，並省下重複的 embedding 比對；「子意圖比對不到動作就直接拒絕」的檢查維持不變。

新增測試 7 個，並以突變測試確認 10 個關鍵行為在還原後會被測試抓到。為配合介面修改 1 個既有測試（`select_actions` 與 `planner.plan` 的假實作接受新參數）。全部非 integration 測試（不含 `tests/kg`）共 921 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

**注意**：尚未在真實 LLM 上執行，LLM 是否穩定保留給定的子意圖需實測（不一致時最多多一次呼叫）。

### 2026-10-11：P-01（commit 7edc41f）

「`select_actions` 不帶 slots 又比對一次」已在 A-04 修正（直接使用第一次的比對結果）。本項處理剩下的 N+1 查詢：

| 項目 | 修正前 | 修正後 | 主要檔案 |
|---|---|---|---|
| 選動作（產生動作清單） | 每個 Action 查參數、查輸出各一次（2N 次） | 所有 Action 以兩個批次查詢（`UNWIND`）取得，已快取的不再查 | `action_store.py`（`get_actions_schema`）、`action_selector.py` |
| 規劃每個步驟 | 每個 atomic 節點查 Action、參數、前置條件、輸出、Agent 共 5 次，同一個 Action 出現幾次就查幾次 | 每個 Action 在快取時效內只查一次；各節點拿到的是副本 | `planner.py` |
| 比對動作 | 每個候選 Action、每個子意圖、每次重規劃都查一次參數 | 經 ActionStore 快取 | `action_store.py` |

快取（`src/kg/cache.py` 的 `TTLCache`）有時效：設定 `[intent] action_cache_ttl_sec`（預設 300 秒，≤ 0 不快取）；常駐服務在重新 seed 後，最晚這麼久會讀到新資料，也可呼叫 `ActionStore.clear_cache()`。查詢失敗不快取。動作清單的參數順序改為依宣告順序（與原本查詢相同），KG 查詢失敗時動作清單改為不列參數，不再中斷規劃。

新增測試 9 個（`tests/intent/test_action_cache.py`），並以突變測試確認 8 個關鍵行為在還原後會被測試抓到。為配合批次查詢修改 1 個既有測試（假 KG 改為回應批次查詢）。全部非 integration 測試（不含 `tests/kg`）共 930 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

### 2026-10-12：P-02（commit 814d66b）

`ActionStore.ensure_action_desc_index`（比對動作前呼叫）改為：

| 情況 | 修正前 | 修正後 |
|---|---|---|
| 每次比對 | `SHOW INDEXES` + `awaitIndex` | 同一個維度只在第一次檢查，之後不查 KG |
| 索引已 ONLINE | 仍呼叫 `awaitIndex` | 不等待 |
| 讀不到維度 | DROP 後重建 | 視為可用 |
| 維度與查詢向量不同 | DROP 後重建 | 記錄錯誤、不刪除，提示重新執行 seed（索引裡的向量是 seed 時算的，比對時重建索引也不會改變它們；seed 本身會在維度不符時重建索引） |
| 索引不存在 | 建立並等待 | 相同（`CREATE ... IF NOT EXISTS`，非破壞性） |

比對時不再刪除或重建索引。向量查詢（主要與備援兩種）都失敗時（例如索引被刪除），下次比對會重新檢查索引；`awaitIndex` 不可用時的輪詢改為每 0.2 秒一次，不再連續送出 30 次查詢。

新增測試 9 個（`tests/intent/test_vector_index.py`），並以突變測試確認 6 個關鍵行為在還原後會被測試抓到。全部非 integration 測試（不含 `tests/kg`）共 939 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

### 2026-10-12：P-03（commit 0dbeb5e）

Neo4j Community 沒有 CDC，仍以輪詢偵測變動，但改善延遲、往返次數與掃描範圍：

| 項目 | 修正前 | 修正後 | 主要檔案 |
|---|---|---|---|
| 延遲 | 最長等一個輪詢週期（2 秒） | 經 BlackboardAgent 的 `write` / `observe` 寫入後立即偵測（`notify_change`）；連續寫入以 `min_poll_gap_sec`（0.1 秒）合併成一輪。直接寫入 Neo4j 的變動（例如導航 benchmark 的事件注入、GuideAgent 的位置）仍由週期輪詢偵測 | `watcher.py`、`agent.py` |
| 每輪查詢 | 每個 Label、每種關係各一次（預設 11 次） | 合成一次 `UNION ALL` 查詢 | `watcher.py`（`build_poll_query`） |
| 掃描範圍 | 所有監控目標 | 只掃描有人訂閱的 Label；Label 開始被訂閱時先建立快取（不發事件），回覆訂閱成功後的變動都會通知；不再被訂閱時丟掉快取 | `watcher.py`、`subscription.py`（`watches_label`） |
| 停止 | 最長等一個輪詢週期 | 立即結束 | `watcher.py` |

BlackboardAgent 預設仍把每個變動廣播到 `blackboard.event.*`（`[blackboard] broadcast_events = true`，行為不變），此時所有監控目標都有訂閱、都會掃描。repo 內沒有程式訂閱這個廣播（IntentionalAgent 用的是個別訂閱轉送的 `blackboard.subscriber.<id>`）；設為 false 後，watcher 只掃描 IntentionalAgent 等訂閱者需要的 Label（例如 Zone、Booth）。

新增測試 8 個（`tests/blackboard/test_watcher.py`，以記憶體中的假黑板模擬），含計時的測試連續重跑 5 次皆通過，並以突變測試確認 10 個關鍵行為在還原後會被測試抓到。全部非 integration 測試（不含 `tests/kg`）共 947 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

**注意**：尚未在真實的 Neo4j / MQTT 上執行（BlackboardAgent 的整合測試需要真實服務）。資料量很大時每輪仍是全量比對，要根本解決需改用 Neo4j Enterprise 的 CDC，或讓所有寫入都經過 BlackboardAgent。

### 2026-10-12：P-04（commit 877f6be）

`src/llm/cache.py` 原本的 `LLMCache`（無上限的 dict，沒有任何地方使用）改寫為 embedding 專用的 `EmbeddingCache`，接進 `LLMClient.embed_text`：

| 項目 | 內容 |
|---|---|
| key | provider、embedding 模型、會影響結果的參數（例如 `dimensions`）、文字；`timeout` 與重試參數不列入 |
| 容量 | LRU，`[llm] embedding_cache_size`（預設 2048 筆，0 表示不快取）；同一模型對同一段文字的向量固定，不設時效 |
| 共用 | 同一個 process 內由 `from_config` 建立的 LLMClient 共用一份（例如 benchmark 每個案例建立新的 IntentionalAgent、比對與重規劃重複出現的子意圖），容量取各設定中最大者 |
| 失敗 | API 失敗不快取；回傳值是副本，呼叫端修改不影響快取 |

對話（chat）回應不快取：重新規劃與前置條件的修正規劃會用相同或相近的 prompt 再問一次，期待得到不同的結果。

新增測試 6 個（`tests/llm/test_embedding_cache.py`），並以突變測試確認 8 個關鍵行為在還原後會被測試抓到。全部非 integration 測試（不含 `tests/kg`）共 953 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

**仍未涵蓋**：快取只在記憶體中，process 結束即消失；若校準工具或 benchmark 需要跨次執行重用，可再加上寫入檔案的快取。

### 2026-10-12：P-05（commit 0037a5c）

| 項目 | 修正前 | 修正後 |
|---|---|---|
| 固定延遲 | 每個非原子子意圖拆解前 `sleep(0.1)` | 移除 |
| 處理順序 | 子意圖逐一處理 | 原子子意圖（不需要 LLM）先建好；非原子的依 Sequence 先後分批，同一批彼此沒有先後，以 thread pool 平行拆解。結果仍依拆解時的順序排列 |
| 呼叫上限 | — | `[intent] planner_max_parallel`（預設 4，1 表示逐一處理）：整個 planner 同時進行的 LLM 拆解呼叫上限，巢狀平行時也共用這個上限 |

步驟之間的資料傳遞（A-03）配合調整：子意圖拆解時可引用的前面步驟，改為「已建好、且不排在它之後的兄弟步驟」：原子兄弟步驟一定看得到（不論在清單中的位置）；同一批平行拆解的非原子兄弟彼此看不到，後面批次看得到前面批次的子樹（`docs/step_dataflow.md` 已更新）。需要前一步結果的子意圖，LLM 應依規則加上 Sequence，就會排在後面的批次。

新增測試 7 個（`tests/intent/test_planner_parallel.py`，含平行、上限、先後、巢狀上限、結果順序、不再 sleep、錯誤傳遞），連續重跑 5 次皆通過，並以突變測試確認 6 個關鍵行為在還原後會被測試抓到。全部非 integration 測試（不含 `tests/kg`）共 960 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

**注意**：尚未以真實 LLM 測量實際縮短的規劃時間；平行呼叫可能觸及 LLM 供應商的速率限制，必要時調低 `planner_max_parallel`。

### 2026-10-12：G-01（commit 4e5ea49）

改為以命名慣例路由，新增執行端 Agent 不必修改核心程式（說明見 `docs/monitoring_protocol.md` §1）：

| 項目 | 修正前 | 修正後 | 主要檔案 |
|---|---|---|---|
| 派工 topic | 只認得 `info` / `navigation`；其他 topic（含空白）一律送往 `info.request`，可能被 InfoAgent 當成未知任務回報成功 | `<channel>.request` 原樣使用、簡寫 `<channel>` 轉成 `<channel>.request`；空白無法派工 | `_executor_utils.py` |
| 結果與進度訂閱 | 固定訂閱 `info.*` 與 `navigation.*` | 依計畫中出現的頻道訂閱 `<channel>.result` / `<channel>.progress`，每個頻道一次；重規劃換上新頻道時派工前再訂閱 | `intentional_agent.py` |
| 取消 | 依 topic 開頭判斷 info 或 navigation | 送往同頻道的 `<channel>.cancel` | `_executor_utils.py`、`intentional_agent.py` |
| 沒有 topic 的步驟 | 送往 info | 規劃驗證時拒絕（`no_topic`）；直接傳入的計畫在派工時記為失敗，不送出 | `intentional_agent.py` |

新增測試 13 個（`tests/test_executor_channels.py`，含新頻道端到端派工與回報、重規劃換上新頻道、取消、沒有 topic），並以突變測試確認 7 個關鍵行為在還原後會被測試抓到。修改 2 個鎖定舊行為的既有測試（`tests/test_execute_plan.py`：空白 topic 不再回傳 `info.request`、未知頻道不再送往 info）。全部非 integration 測試（不含 `tests/kg`）共 973 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

**仍未涵蓋**：InfoAgent 與 NavigationAgent 處理派工、取消、冪等與結果的程式重複；可再抽成共用的執行端基底類別，讓新增執行端只需實作任務本身。

### 2026-10-12：G-02（commit ca497df）

L-01 已讓 IntentionalAgent 依 `DomainProfile.env_subscriptions` 覆寫前綴，本項移除其餘的領域綁定：

| 項目 | 修正前 | 修正後 | 主要檔案 |
|---|---|---|---|
| 預設值 | `TriggerConfig.relevant_topic_prefixes = ("Zone/", "Booth/")` | 預設不綁定任何領域：沒有設定時，收到的事件都視為相關（IntentionalAgent 只訂閱 `env_subscriptions`） | `trigger.py` |
| 相關性判斷 | 把訂閱 pattern 截成前綴（`"Zone/*/*"` → `"Zone/"`），`"*/CONNECTED_TO/*"` 變成空前綴而比對所有事件，`"ZoneX/..."` 也會被 `"Zone"` 類前綴誤中 | 新增 `relevant_topic_patterns`，以與黑板訂閱相同的語意比對（`*` 比對任一段、段數相同）；IntentionalAgent 直接傳入 `env_subscriptions`。`relevant_topic_prefixes` 保留給直接設定的呼叫端 | `trigger.py`、`intentional_agent.py` |
| 事件中的實體 | metadata 欄位寫死 `zone`、`booth_id`、`zone_name`、`target`（其中沒有任何事件來源會產生） | 預設為 BlackboardWatcher 的 `node_id`、`source_id`，可由 `TriggerConfig.entity_metadata_keys` / `DomainProfile.env_entity_keys` 擴充 | `trigger.py`、`domain_profile.py` |

新增測試 10 個（`tests/monitoring/test_trigger.py`：pattern 語意、預設不綁領域、其他領域（會議室）的事件、可設定的實體欄位），並以突變測試確認 6 個關鍵行為在還原後會被測試抓到。修改 3 個依賴舊預設的既有測試（改為明確設定 pattern）。全部非 integration 測試（不含 `tests/kg`）共 983 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

### 2026-10-12：G-03（commit 450b8ee）

| 項目 | 修正前 | 修正後 | 主要檔案 |
|---|---|---|---|
| 觀察結果的領域寫入 | `_apply_observer_updates` 寫死「人流 → Zone 人潮狀態」「設施事件 → Booth 狀態」 | 改為投影規則 `ObservationProjection(observation_type, entity_type, build)`；核心只負責套用，單筆失敗不影響其他實體。展場規則移到預設集 `venue` | `projection.py`（新）、`agent.py` |
| 規則來源 | — | 建構參數 `projections=`，或 `[blackboard] projections`（預設集名稱清單）；其他領域以 `register_projection_preset` 登記。未設定時沿用 `["venue"]`（既有啟動腳本行為不變；規則只對對應的觀察類型與實體類型生效，不影響其他領域） | `projection.py`、`agent.py` |
| 監看目標 | watcher 固定監看 Agent、Zone、Task、POI、Booth | 建構參數 `watch_targets=` 或 `[[blackboard.watch_targets]]`；未設定時沿用內建目標 | `watcher.py`（`WatchTarget.from_config`）、`agent.py` |

設定與擴充方式見新文件 [`docs/blackboard_agent.md`](blackboard_agent.md)。

新增測試 6 個（`tests/blackboard/test_projection.py`），並以突變測試確認 7 個關鍵行為在還原後會被測試抓到。全部非 integration 測試（不含 `tests/kg`）共 989 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

### 2026-10-12：S-01（commit 2a36125）

依確認的方向：共用金鑰＋唯讀查詢＋封鎖危險語法；未設定金鑰時照常運作並在啟動時警告。說明見 [`docs/blackboard_agent.md`](blackboard_agent.md) §4。

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 共用金鑰 | `[blackboard.access] token`：設定後 `blackboard.control` 的每個命令（query、write、subscribe、observe…）都要帶相同的 `token`（以 `hmac.compare_digest` 比對），否則回覆 unauthorized。未設定時照常運作，啟動時記錄警告 | `access.py`（新）、`agent.py` |
| 用戶端自動附上 | client helper（IntentionalAgent、InfoAgent、NavigationAgent 經此查詢與寫入）、導航感測器、觀察者從自己的設定讀取金鑰並附上 | `client.py`、`navigation_scenario/sensors/base.py`、`src/observer/base.py` |
| 唯讀查詢 | `query` 明確以唯讀交易執行（`kg.read`），Neo4j 會拒絕其中的寫入 | `agent.py` |
| 危險語法 | 不論金鑰，一律拒絕 `LOAD CSV`、`dbms.*` / `apoc.*` 程序、索引與約束的建立刪除、資料庫與使用者管理、權限設定、`USE` 切換資料庫 | `access.py` |

新增測試 22 個（`tests/blackboard/test_access.py`，含「repo 中所有經黑板代理執行的 Cypher 都不會被誤擋」），並以突變測試確認 12 個關鍵行為在還原後會被測試抓到。全部非 integration 測試（不含 `tests/kg`）共 1011 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

**注意**：
- 要啟用防護，需在 `gias.toml` 加上 `[blackboard.access] token`（所有元件共用同一份設定即可）；本次未修改 `gias.toml`
- 金鑰隨 MQTT 明文傳送，broker 未啟用 TLS 時可被竊聽（S-02）
- 持有金鑰的用戶端仍可執行任意（非危險語法的）寫入，例如刪除黑板節點；更細的權限（依用戶端限制可寫入的 Label）尚未實作

### 2026-10-12：S-02（commit c11bdc6）

依確認的方向：TLS 支援加在 AgentFlow 的 MQTT broker；ACL 採 GIAS 共用服務帳號、拒絕其他連線。完整步驟見 [`docs/mqtt_security.md`](mqtt_security.md)。

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 用戶端 TLS（AgentFlow） | MQTT broker 設定新增 `tls`：`true`（系統 CA）或 table（`ca_certs`、`certfile` / `keyfile`、`keyfile_password`、`ciphers`、`insecure`、`enabled`）；在連線前呼叫 `tls_set`（強制驗證 broker 憑證、TLS client 協定），`insecure` 才關閉主機名稱驗證。設定有誤或憑證讀不到時啟動失敗，不退回明文 | `D:\Work\SDK\AgentFlow` 的 `src/agentflow/broker/mqtt_broker.py`、`tests/unit/test_mqtt_broker_tls.py`（新） |
| broker 設定範例 | Mosquitto：TLS listener（8883）、關閉匿名、密碼檔、ACL（GIAS 服務帳號可讀寫全部 topic，其他帳號只能用列出的 topic） | `deploy/mosquitto/mosquitto_tls.conf`、`deploy/mosquitto/gias_acl.conf`（新） |
| 啟動檢查 | IntentionalAgent 與 BlackboardAgent 啟動時檢查 broker 設定：沒有 TLS、沒有帳號、或 TLS 不驗證主機名稱時記錄提醒（localhost 記為 INFO，其他主機記為 WARNING） | `src/security.py`（新）、`intentional_agent.py`、`blackboard/agent.py` |
| 文件 | 憑證產生、帳號與 broker 設定、`gias.toml` 的 TLS 設定、確認方式 | `docs/mqtt_security.md`（新） |

新增測試：GIAS 16 個（`tests/test_security.py`）、AgentFlow 13 個（`test_mqtt_broker_tls.py`，AgentFlow 全部單元測試 741 個通過），並以突變測試確認兩邊共 9 個關鍵行為在還原後會被測試抓到。GIAS 全部非 integration 測試（不含 `tests/kg`）共 1027 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

**注意**：
- AgentFlow 的修改在另一個 repo，尚未 commit；GIAS 的 TLS 設定需要包含這個修改的 AgentFlow
- 本次未修改 `gias.toml` 與 broker 實際設定；啟用時依 `docs/mqtt_security.md` 設定 broker 與 `gias.toml`
- 未在真實的 TLS broker 上連線測試
- 所有元件共用一個服務帳號；依角色分帳號尚未實作

### 2026-10-12：S-03（commit f7f2251）

機密改由環境變數提供，設定檔只寫參照。說明見 [`docs/secrets.md`](secrets.md)。

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 環境變數參照 | `get_agent_config()` 載入時把設定值中的 `${VAR}` 換成環境變數（dict / list 遞迴）；未設定的變數換成空字串 | `src/app_helper.py` |
| 明文提醒 | 載入時若 `api_key`、`password`、`token`、`secret`、`keyfile_password` 以明文寫在檔案中，或參照的環境變數未設定，記錄提醒（每個 process、每個設定檔一次；只列鍵名，不輸出值） | `src/app_helper.py` |
| OpenAI 金鑰 | 設定中沒有 `api_key`（或為空）時讀 `OPENAI_API_KEY` 環境變數；設定中的值優先 | `src/llm/config.py` |
| 設定範本 | 不含機密的 `gias.example.toml`（所有機密寫成 `${...}`），可放進版本控制 | `gias.example.toml`（新） |

檢查結果：版本控制中沒有被追蹤的金鑰；`_bak/*.7z` 備份只含 `src`，沒有 `gias.toml`（以封存檔清單確認）。

新增測試 5 個（`tests/test_config_secrets.py`），並以突變測試確認 7 個關鍵行為在還原後會被測試抓到。全部非 integration 測試（不含 `tests/kg`）共 1032 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

**注意**：本次未修改 `gias.toml`，其中的 MQTT 密碼、OpenAI 金鑰、Neo4j 密碼仍為明文，元件啟動時會出現提醒；依 `docs/secrets.md` §3 搬到環境變數，並更換曾以明文存放的金鑰與密碼。

### 2026-10-12：S-04（commit b01fdf7）

說明見 [`docs/prompt_safety.md`](prompt_safety.md)。

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 資料區塊與規則 | 使用者輸入、執行端回覆、黑板與感測器資料放進 prompt 時包在 `<user_input>` / `<step_results>` / `<env_data>` 中，跳脫文字中的同名標記、移除控制字元、限制長度；各 prompt 的 system 規則說明標記內是資料，不得照做其中的指示 | `src/llm/untrusted.py`（新）、`prompt_builder.py`、`llm_decomposer.py`、`scope_gate.py`、`llm_advisor.py`、`intention/response.py`、意圖解析模板與 `intent_tasks.py` |
| 模板角色注入 | 修正：`PromptRegistry.render` 原本先代入變數再依 `---system` 切分角色，使用者輸入中的 `---system` 一行就能新增 system 訊息；改為先切分再代入 | `src/llm/prompts/registry.py` |
| 警示 | 比對常見注入語句（中英文），`plan_intention` 記錄 warning 並標在 `debug.prompt_injection_signals`；不據此拒絕 | `untrusted.py`、`intentional_agent.py` |

LLM 輸出原本就受白名單動作、參數 schema、前置條件、ScopeGate 等結構驗證約束，文件中說明各層分工。

新增測試 18 個（`tests/llm/test_untrusted.py`），並以突變測試確認 12 個關鍵行為在還原後會被測試抓到。全部非 integration 測試（不含 `tests/kg`）共 1050 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

**限制**：資料區塊與規則只能降低、不能完全消除 LLM 被誘導的可能；警示是關鍵字比對，可被改寫繞過；尚未以真實 LLM 測試防護效果。

### 2026-10-12：E-02（commit a873e97）

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 開發流程 | 以 git 取代 7z 備份：分支保存進度、`git stash` 擱置、`git tag` 標記里程碑、`git switch --detach` / `git worktree` 取回舊版、`git diff` 比較版本；`_bak/` 確認版本都在 git 後移到 repo 外封存 | `CONTRIBUTING.md`（新） |
| Commit 訊息 | 慣例：第一行用祈使句說明做了什麼，對應問題時加編號；內文說明原因。`commit-msg` hook 擋下少於 10 個位元組、只有標點或數字、重複同一字元、或只是 wip / update / fix / test / tmp 等佔位詞的第一行；Merge、Revert、fixup! / squash! 不檢查；第一行超過 100 位元組時只提醒 | `.githooks/commit-msg`（新）、`.gitattributes`（新，hook 保持 LF 換行） |

檢查結果：git 歷史 67 個 commit 中，hook 會擋下的只有早期 9 個（`..`、`aaa`、`Init 2` 等）；這些 commit 已在 GitHub 遠端，改寫需要 force push，因此不更動歷史。

新增測試 23 個（`tests/test_commit_msg_hook.py`，找不到 `sh` 時略過），並以突變測試確認 hook 的 5 條規則在還原後會被測試抓到。全部非 integration 測試（不含 `tests/kg`）共 1073 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

**注意**：hook 需要在每個 clone 執行一次 `git config core.hooksPath .githooks` 才會啟用；本次未修改 repo 的 git 設定，也未搬移 `_bak/`。

### 2026-10-12：E-03（commit 379b75d）

依確認的方向：刪除未使用的模組，包含 KGClient。

| 模組 | 處理 |
|---|---|
| `llm/cache.py` | 不需處理：P-04 已改寫為 embedding 快取並使用中 |
| `llm/router.py`、`llm/policy.py` | 刪除：沒有任何地方使用；`Policy` 呼叫的 `llm_client.call()`、`response.cost` 不存在（重試與逾時已由 `LLMClient` 實作） |
| `llm/observability.py` | 刪除：400 行、38 個未被呼叫的 log 函式 |
| `llm/schemas/plan.py`、`schemas/eval.py`、`tasks/planning_tasks.py`、`tasks/evaluation_tasks.py` | 刪除：各只有一行註解 |
| `kg/client.py`（KGClient）、`kg/queries.py`、`kg/commands.py` 與 `tests/kg` 的對應測試 | 刪除：正式程式都經 `Neo4jBoltAdapter`、`ActionStore` 與黑板 client 存取 KG；`kg/__init__.py` 原本寫「所有存取必須經由 KGClient」，已改為符合現況的說明 |

順帶修正：
- `tests/kg/test_adapter.py` 的 patch 路徑寫成 `kg.adapter_neo4j`（應為 `src.kg.adapter_neo4j`），6 個 `Neo4jBoltAdapter` 測試一直無法執行；修正後 `tests/kg` 不必再排除在回歸測試之外（`CONTRIBUTING.md` 的測試指令已更新）
- `llm/providers/mock_provider.py` 從 `..client` 匯入不存在的 `LLMUsage`，`[llm] provider = "mock"` 時匯入失敗；改從 `..types` 匯入

刪除約 1,800 行（程式與測試），仍可從 git 取回。全部非 integration 測試（含 `tests/kg`）共 1079 個通過、10 個略過、0 個錯誤；`test_log_colors_visual` 1 個失敗為既有問題。

### 2026-10-12：E-04（commit 8aa32f6）

| 項目 | 修正前 | 修正後 | 主要檔案 |
|---|---|---|---|
| 執行期的 print | InfoAgent、NavigationAgent 每個進度同時 print 一次、logger 再記一次；planner 以 print 輸出拆解過程 | 改為只用 logger（進度、收到請求、完成結果都在同一行 log 中）；planner 的拆解過程經它的 logger 輸出，emoji 標記改為 `[EXEC]` / `[NEW]`（避免 cp950 主控台編碼錯誤） | `info_agent.py`、`navigation_agent.py`、`planner.py` |
| 等級控制 | `init_logging()` 只設定 `gias` logger；以 `logging.getLogger(__name__)` 取得的模組 logger（如 `src.kg.action_store`、`src.app_helper`、監測子系統的預設 logger）沒有 handler，INFO 以下被丟棄、警告以未格式化的方式輸出，不受 `LOG_LEVEL` 控制 | `src.*`、`navigation_scenario.*` 的模組 logger 與 `gias` 共用同一個 handler（含機密遮罩）與等級；pytest 模式交回 root，讓 caplog 收得到 | `src/log_helper.py` |
| 慣例 | — | `CONTRIBUTING.md` 說明：執行期程式用 logger；`print` 只用在命令列的輸出（啟動腳本、seed、工具、BlackboardAgent 啟動摘要、單次模式給使用者的回覆） | `CONTRIBUTING.md` |

新增測試 5 個（`tests/test_logging_config.py`），並以突變測試確認 4 個關鍵行為在還原後會被測試抓到。全部非 integration 測試共 1084 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

### 2026-10-12：E-05（commit 2be2b52）

`[llm.openai]` 新增三個設定，與 `[llm.ollama] model` 對稱；未設定時行為不變：

| 設定 | 預設 | 說明 |
|---|---|---|
| `model` | `gpt-4.1-mini` | 聊天模型（規劃、ScopeGate、監測判斷、最後回覆）；個別呼叫仍可用 `model=` 覆寫 |
| `base_url` | 環境變數 `OPENAI_BASE_URL` | 相容 OpenAI API 的其他端點 |
| `use_responses` | 環境變數 `GIAS_OPENAI_USE_RESPONSES` | true 改用 Responses API |

主要檔案：`src/llm/config.py`、`src/llm/providers/factory.py`、`openai_provider.py`；`gias.example.toml` 已加上這些設定（本次未修改 `gias.toml`）。

新增測試 8 個（`tests/llm/test_openai_config.py`），並以突變測試確認 4 個關鍵行為在還原後會被測試抓到。全部非 integration 測試共 1092 個通過、10 個略過；`test_log_colors_visual` 1 個失敗為既有問題。

### 2026-10-12：E-06（commit e351ef5）

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| CI | GitHub Actions：push 到 `main`、pull request、手動觸發時，在 Ubuntu 與 Windows（Python 3.14）安裝相依套件並執行離線測試（`-m "not integration"`） | `.github/workflows/tests.yml`（新） |
| 相依套件 | repo 原本沒有相依套件清單；新增 `requirements.txt`（目前測試過的版本；AgentFlow 以 GitHub 上的 commit 安裝）與 `requirements-dev.txt`（加上 pytest） | `requirements.txt`、`requirements-dev.txt`（新） |
| 測試不依賴本機設定 | `tests/test_execute_plan.py` 的備援設定缺少 `neo4j_actions`，沒有 `gias.toml` 時 12 個測試失敗；觀察者整合測試在沒有 `gias.toml` 時出錯（改為略過，與連不到 broker 時一致） | `tests/test_execute_plan.py`、`tests/observer/test_observer_integration.py` |
| 既有失敗 | `test_log_colors_visual` 原本只能搭配 `-s` 目視（pytest 下 log 交給 root，不寫到 stdout/stderr），一直失敗；改為掛自己的 stderr handler，平常可驗證、`-s` 時仍可目視 | `tests/test_log_colors.py` |

驗證：
- 在全新虛擬環境依 `requirements-dev.txt` 安裝（AgentFlow 從 GitHub 安裝），於沒有 `gias.toml` 的工作目錄執行：1093 個通過、10 個略過、0 個失敗
- GIAS 測試在 AgentFlow GitHub `main`（61e92c9）與本機 AgentFlow 上結果相同
- 本機（有 `gias.toml`）完整回歸：1093 個通過、10 個略過、0 個失敗

**注意**：
- CI 在 push 到 GitHub 後才會執行；本機 `main` 目前比 `origin/main` 多 50 個以上的 commit，尚未 push
- 未在 Linux 上實際執行過；第一次 CI 執行會驗證 Ubuntu 與 Windows 兩個環境
- AgentFlow 固定在 GitHub 上的 commit；S-02 的 TLS 修改在 AgentFlow 端 commit 並 push 後，需更新 `requirements.txt` 中的 commit

### 2026-10-12：V-03（commit 1d7d19e）

依選擇，不改寫 experiment1 的執行方式，而是明確標示它只驗證規劃，並把執行與調適的端到端驗證交給 navigation_scenario 的 live 模式。

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 範圍定義 | `mode = planning_only`：規劃經過 GIAS，執行與重規劃是 Dijkstra 模擬；列出各指標的來源（只有 PE 由 GIAS 產生的計畫決定）與取代者 | `experiment1/scope.py`（新） |
| 結果 JSON | 新的結果檔加上 `mode`、`validation_scope`、`superseded_by`；執行時先印出範圍說明 | `experiment1/batch_runner.py` |
| Summary | 終端機與 `summary.md` 標示 Mode 與範圍說明，Notes 逐項註明指標來源；CSV 欄位不變。已重新產生 `results/summary.md`（數字不變） | `experiment1/analyze.py`、`experiment1/results/summary.md` |
| 文件 | README 新增「驗證範圍」一節：各階段的做法、哪些指標是模擬、既有結果同樣適用、與 navigation_scenario 的對應與差異。指標表加上來源欄，runner 與 metrics 的說明同步更新 | `experiment1/README.md`、`runner.py`、`metrics.py` |

檢查時發現：模擬的路徑依測試案例的 `goal_zones` 計算，完全不讀計畫內容，所以計畫只影響 PE（PE 不通過的案例記為失敗）。TSR、ISR、RSR、constraint_rate、路徑長度與時間都是模擬結果。

新增測試 8 個（`tests/experiment1/test_experiment1_scope.py`），並以突變測試確認 8 個關鍵行為在還原後會被測試抓到。全部非 integration 測試共 1101 個通過、10 個略過、0 個失敗。

**注意**：
- experiment1 的程式與既有結果都保留；既有的結果 JSON 沒有改寫（沒有 mode 欄位，範圍相同）
- 取代者 navigation_scenario 的 live 模式（V-01）尚未在真實服務上執行（OpenAI 金鑰目前回 401），執行與調適的實測數據要等它跑過才有
- experiment1 的 30%／60% 是事件注入機率，navigation_scenario 的 30%／60% 是注入時的任務進度，兩者的數字不能直接比較

### 2026-10-12：V-04（commit ccbcdbd）

依選擇，不改 GenExam 的執行方式，而是明確標示它是獨立的出題管線：只用到 GIAS 的 LLMClient 與 Blackboard KG，規劃、執行與修正都是 runner 的固定流程，沒有經過 IntentionalAgent 的規劃與監測。

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 範圍定義 | `scope = standalone_pipeline`，各階段都是 `fixed_pipeline`；列出用到與沒用到的 GIAS 元件，以及各指標實際量到的內容 | `genexam_scenario/scope.py`（新） |
| 結果 JSON | 每案結果 JSON 與 `_manifest.json` 加上 `scope`、`validation_scope`、`gias_components`；manifest 原有的 `mode`（dry-run／live，指是否呼叫 LLM）不變。單案例與批次執行時先印出範圍說明 | `runner.py`、`batch_runner.py` |
| 報告 | `report.md` 在主表前標示範圍，主表後列出各指標實際量到的內容；終端輸出也印出範圍說明 | `analyze.py` |
| 文件 | README 新增「驗證範圍」一節（用到與沒用到的 GIAS 元件、指標的解讀）。開頭原本寫會在生成中途注入約束，實際上限制在生成前固定，已修正；Action KG 一節註明 runner 沒有使用這些 action。套件與測試案例的說明同步更新 | `README.md`、`__init__.py`、`test_cases.py` |

檢查時發現：
- `seed_actions` 寫入的 11 個 GVR action（含 UpdateIntention）和 Blackboard 上的 GVR Agent 節點，目前都沒有程式使用
- CSR 與 SC 只計算通過驗證的題目，並沿用驗證時的同一套規則，所以只要有題目通過，SC 就是 100%；未使用 `--llm-judge` 時 CSR 也是 100%（比對的是 LLM 自己標示的難度／Bloom／題型），Task Success 實際上等於題數是否達標。報告中 CSR、SC 的 100% 是計算方式決定的，不能當作題目實際符合限制的證據。本次只標示，沒有改計算方式

新增測試 11 個（`tests/genexam_scenario/test_genexam_scope.py`），其中一個確認上述 CSR／SC 的前提，計算方式改變時會提醒更新標示；並以突變測試確認 10 個關鍵行為在還原後會被測試抓到。全部非 integration 測試共 1112 個通過、10 個略過、0 個失敗。

**注意**：
- `genexam_scenario_results/` 不在版本控制內；本機既有的結果與報告沒有重新產生，重跑 `python -m genexam_scenario.analyze <run_dir>` 即可加上標示
- GenExam 沒有像 navigation_scenario 那樣的 live 模式，GIAS 在試題生成這個領域仍沒有端到端驗證

### 2026-10-12：V-05（不處理）

決定不處理，沒有修改程式。理由：
- repo 中沒有任何消融實驗（LLM-only、一般 RAG、拿掉 IPM、拿掉 MAS）的程式、設定或結果檔，`IPM` 這個名稱在程式碼中也沒有出現，沒有可沿用的基礎
- 這些對照實驗只用於驗證各元件的貢獻，不影響框架本身的功能

若之後需要對照組，較可行的做法是在 navigation_scenario 的 live 模式加上可切換的變體（例如關閉監測與重規劃、不用 Action KG 比對、只用 LLM 規劃），並在結果中標示變體名稱；屆時另開項目。「一般 RAG」與「拿掉 MAS」需要另外設計。

### 2026-10-12：V-06（commit e31e100）

依選擇，內建執行端維持示範用途，但「模擬」標記改為正確，並傳到意圖層：

| 項目 | 內容 | 主要檔案 |
|---|---|---|
| 執行端 | `build_result` 的 `simulated` 改為參數（預設 `false`），不再一律為 `true`。執行端以 `is_simulated(task)` 逐個 task 聲明：InfoAgent、NavigationAgent 的 task 都是模擬；GuideAgent 的移動類 task 實際在 Blackboard 圖上移動，不標模擬，其餘 task 沿用 NavigationAgent 的模擬；缺少 task 而拒絕的請求不標模擬 | `_executor_utils.py`、`info_agent.py`、`navigation_agent.py`、`navigation_scenario/live/guide_agent.py` |
| 意圖層 | 監測迴圈原本在把結果轉成 `ActionResult` 時丟掉 `simulated`；現在保留並記在節點上。執行摘要（監測與同步兩條路徑）新增 `simulated_steps`：已完成、但執行端回報為模擬的步驟 id；每個步驟的結果也帶 `simulated`。意圖的 `result` 一併帶出 `simulated_steps`。`ok` 的定義不變 | `events.py`（`is_simulated_result`）、`cursor.py`、`intentional_agent.py`、`intention/manager.py` |
| 最後回覆 | `template` 在模擬步驟後加「（模擬執行）」並附註「並未實際完成」；`llm` 的 prompt 標明哪些步驟只是模擬、要求說明，沒有模擬步驟時 prompt 不變 | `intention/response.py` |
| 文件 | 結果 payload 的 `simulated` 欄位、意圖結果的 `simulated_steps`、回覆的模擬註記；`src/agents` 說明改為示範用執行端（原本寫「實際帶領/導航」） | `docs/monitoring_protocol.md` §3、`docs/intention_protocol.md`、`docs/step_dataflow.md` §4、`src/agents/__init__.py` |

新增測試 14 個（`tests/test_simulated_results.py`），其中一個把 InfoAgent 與 GuideAgent 實際接上監測迴圈，確認標記一路傳到執行摘要、意圖結果與回覆（這個測試抓到了 `ActionResult` 丟掉標記的問題）；並以突變測試確認 16 個關鍵行為在還原後會被測試抓到。全部非 integration 測試共 1126 個通過、10 個略過、0 個失敗。

**注意**：
- `build_result` 的預設由 `simulated=true` 改為 `false`：依頻道慣例新增的執行端若只回固定訊息，需自行傳 `simulated=True`（或覆寫 `is_simulated`）
- 內建執行端仍是示範用途；實際部署時依頻道慣例接上自己的執行端

---

## 附錄：C-01 重現腳本

模擬 RecursivePlanner 的輸出（LLM 在每一層都從 "1" 開始編號）。在專案根目錄用 gias 環境的 Python 執行（存成檔案執行時需設定 `PYTHONPATH=.`）。下列輸出是修正前的結果；修正後 `PlanCursor(plan)` 會直接拋出 `DuplicateNodeIdError`：

```python
from src.core.monitoring.cursor import PlanCursor

plan = {"id": "root", "type": "composite",
        "execution_logic": [{"type": "Sequence", "from_id": "1", "to_id": "2"}],
        "sub_plans": [
            {"id": "1", "type": "atomic", "is_atomic": True, "intent": "A", "task": "TaskA"},
            {"id": "2", "type": "composite", "execution_logic": [], "sub_plans": [
                {"id": "1", "type": "atomic", "is_atomic": True, "intent": "B1", "task": "TaskB1"},
                {"id": "2", "type": "atomic", "is_atomic": True, "intent": "B2", "task": "TaskB2"}]}]}

c = PlanCursor(plan)
print([(r.node_id, r.node["task"]) for r in c.all_records()])
# 實際輸出：[('1', 'TaskA'), ('2', 'TaskB2')]  ← TaskB1 被靜默丟棄

for _ in range(2):
    for rec in c.next_ready_atomics():
        tid = "t-" + rec.node_id
        c.mark_dispatched(rec.node_id, task_id=tid)
        c.accept_action_result(tid, {"ok": True})
print(c.done(), c.summary()["ok"])
# 實際輸出：True True  ← TaskB1 從未執行，卻回報成功
```

# GIAS 問題清單

- **建立日期**：2026-10-09
- **分析基準**：`main` @ `51ea57f`，加上工作目錄中尚未 commit 的修改（含 v2 監測子系統 `src/core/monitoring/`）
- **測試現況**：離線單元測試（`tests/monitoring`、`tests/navigation_scenario`、`tests/agents`、`tests/test_execute_plan_with_monitoring.py`）共 548 個，全部通過
- **總數**：42 項（嚴重 5／高 11／中 15／低 11）

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
| **C** 正確性 | C-01, C-02 | C-03, C-04 | C-05 ~ C-08 | C-09 |
| **A** 架構與論文主張的落差 | — | A-01, A-02 | A-03, A-04 | — |
| **P** 效能與成本 | — | — | P-01 ~ P-03 | P-04, P-05 |
| **G** 通用性與耦合 | — | — | G-01, G-02 | G-03 |
| **S** 安全 | — | S-01 | S-02, S-03 | S-04 |
| **E** 工程品質 | — | E-01 | — | E-02 ~ E-06 |
| **合計** | **5** | **11** | **15** | **11** |

---

## 一、嚴重（5 項）

| 編號 | 類別 | 問題 | 位置 | 影響 | 狀態 |
|---|---|---|---|---|---|
| **V-01** | 實驗驗證 | 導航實驗完全沒呼叫 IntentionalAgent，改用 Dijkstra 模擬，失敗則依 `replan_failure_probability` 隨機注入 | [runner.py:18](../navigation_scenario/runner.py#L18) | TSR 95%、RSR 80.9% 量到的是圖演算法，不是 GIAS 的重規劃 | 未處理 |
| **V-02** | 實驗驗證 | 核心的監測執行路徑 `execute_plan_with_monitoring` 只有 mock 單元測試，沒有任何端到端實驗跑過 | [intentional_agent.py:508](../src/core/intentional_agent.py#L508) | 「監控與重新考慮」這項主張缺乏實驗支持 | 未處理 |
| **L-01** | 閉環／監測 | 黑板環境事件沒有接進監測器：全 repo 沒有地方訂閱 `on_env_event`，`_bb_subs_active` 也從未使用（檔頭註解宣稱有訂閱） | [monitor.py:52](../src/core/monitoring/monitor.py#L52)、[intentional_agent.py:99](../src/core/intentional_agent.py#L99) | 環境改變永遠不會觸發重規劃，系統只對動作失敗反應，閉環沒有成立 | 已修正（未 commit） |
| **C-01** | 正確性 | 巢狀計畫的節點 ID 衝突：LLM 每層都從 "1" 開始編號，cursor 遇到重複 ID 就靜默略過（已實測重現，見附錄） | [planner.py:145](../src/core/intent/planner.py#L145)、[cursor.py:105](../src/core/monitoring/cursor.py#L105) | 子任務沒被執行，最後卻回報 `ok=True` | 已修正（未 commit） |
| **C-02** | 正確性 | 沒有 action 或 topic 的 atomic 節點（`leaf_no_children`、`leaf_forced_atomic`，或 action 為空字串的節點）會繞過白名單檢查，被預設送到 `info.request`，而 InfoAgent 對未知 task 一律回成功 | [planner.py:122](../src/core/intent/planner.py#L122)、[planner.py:139](../src/core/intent/planner.py#L139)、[_executor_utils.py:55](../src/agents/_executor_utils.py#L55)、[info_agent.py:200](../src/agents/info_agent.py#L200) | 根本無法執行的步驟被記為成功（假成功） | 已修正（未 commit） |

## 二、高（11 項）

| 編號 | 類別 | 問題 | 位置 | 影響 | 狀態 |
|---|---|---|---|---|---|
| **V-03** | 實驗驗證 | experiment1 只用 `plan_intention()` 產生計畫，執行與重規劃都是 Dijkstra 模擬 | [experiment1/runner.py](../experiment1/runner.py) | 只驗證了規劃，沒驗證執行與調適 | 未處理 |
| **V-04** | 實驗驗證 | GenExam 是 runner 內另外寫的一條「計畫、檢索、生成、驗證、修正」管線，沒有經過 IntentionalAgent | [genexam_scenario/runner.py](../genexam_scenario/runner.py) | 試題生成的結果不能直接歸功於 GIAS 這個通用框架 | 未處理 |
| **V-05** | 實驗驗證 | 論文提到的消融實驗（LLM-only、一般 RAG、拿掉 IPM、拿掉 MAS）在 repo 裡找不到程式 | — | 無法重現；若程式放在別處，請一併納入 | 未處理 |
| **L-02** | 閉環／監測 | 子樹和根層的重規劃都不使用 `env_facts`，等於用同樣的輸入再問一次 LLM | [intentional_agent.py:788](../src/core/intentional_agent.py#L788) | 重規劃很可能得到同樣的計畫 | 已修正（未 commit） |
| **L-03** | 閉環／監測 | LLM 輔助觸發沒有接上：`enable_llm_assist` 預設為 False，也沒有注入 `llm_decider` | [trigger.py:35](../src/core/monitoring/trigger.py#L35)、[intentional_agent.py:542](../src/core/intentional_agent.py#L542) | REPAIR_NODE 分支永遠走不到 | 已修正（未 commit） |
| **C-03** | 正確性 | ScopeGate 的 `decide()` 自己吞掉例外並回傳 `can_execute=True`，所以 strict 設定無效 | [scope_gate.py:53](../src/core/intent/scope_gate.py#L53)、[intentional_agent.py:329](../src/core/intentional_agent.py#L329) | `scope_gate_strict = true` 形同虛設，判斷出錯時會直接放行 | 已修正（未 commit） |
| **C-04** | 正確性 | 節點沒有逾時機制，`deadline_sec` 預設為 None | [intentional_agent.py:613](../src/core/intentional_agent.py#L613)、[budget.py:20](../src/core/monitoring/budget.py#L20) | 執行端掛掉或訊息遺失時，迴圈會永遠等待 | 已修正（未 commit） |
| **A-01** | 架構落差 | 意圖只是一個字串：沒有 Intention 模型和生命週期狀態，不支援多意圖、優先序或衝突管理，Agent 執行完就結束 | [intentional_agent.py:70](../src/core/intentional_agent.py#L70)、[intentional_agent.py:136](../src/core/intentional_agent.py#L136) | 「跨時間維持意圖」與 Bratman 意圖理論的主張沒有落實 | 未處理 |
| **A-02** | 架構落差 | 前置條件驗證沒有實作：Action KG 沒有前置條件和效果，`preconditions_by_action`、`conflicts_between_intents` 已寫好卻從未被呼叫 | [queries.py:211](../src/kg/queries.py#L211)、[queries.py:233](../src/kg/queries.py#L233) | 摘要所說「KG-RAG 提供前置條件驗證」與程式不符；LLM 產生的計畫沒有符號層面的驗證 | 未處理 |
| **S-01** | 安全 | `blackboard.control` 會執行任何 MQTT 用戶端送來的 Cypher，沒有白名單也沒有權限控管 | [blackboard/agent.py:212](../src/blackboard/agent.py#L212)、[blackboard/agent.py:232](../src/blackboard/agent.py#L232) | 任何連得上 broker 的人都能讀取或刪除整個黑板 | 未處理 |
| **E-01** | 工程品質 | 核心監測子系統 `src/core/monitoring/` 還沒加入 git 追蹤，另有約 1,100 行修改未 commit | `src/core/monitoring/` | 程式可能遺失，版本也無法追溯 | 未處理 |

## 三、中（15 項）

| 編號 | 類別 | 問題 | 位置 | 影響 | 狀態 |
|---|---|---|---|---|---|
| **V-06** | 實驗驗證 | 執行端 Agent 都是模擬的：回傳 `"simulated": True` 和固定訊息 | [_executor_utils.py:158](../src/agents/_executor_utils.py#L158) | 「任務成功」不代表任務真的完成 | 未處理 |
| **L-04** | 閉環／監測 | 動態調適發生在 NavigationAgent 內部（自己查兩次黑板），意圖層並不知道 | [navigation_agent.py:98](../src/agents/navigation_agent.py#L98) | 調適能力歸屬在執行端，不在 IPM，和架構敘述不一致 | 未處理 |
| **C-05** | 正確性 | 有依賴循環時，相關節點會被當成孤兒放到第一層先執行，而且沒有警告 | [cursor.py:191](../src/core/monitoring/cursor.py#L191) | 違反執行順序 | 未處理 |
| **C-06** | 正確性 | 比對器查不到時把門檻降為 0 重查，參數 gate 全部淘汰時也會關掉 gate 重試 | [action_matcher.py:243](../src/core/intent/action_matcher.py#L243)、[action_matcher.py:343](../src/core/intent/action_matcher.py#L343) | 比對器幾乎不會拒絕，「超出能力範圍」只能靠 ScopeGate 一次 LLM 判斷 | 未處理 |
| **C-07** | 正確性 | 參數用正規表示式從 LLM 字串解析，不依 `params_schema` 驗證型別、enum 或必填 | [planner.py:23](../src/core/intent/planner.py#L23) | 例如 `Interests=null` 會被當成字串 "null" 傳下去 | 未處理 |
| **C-08** | 正確性 | 判斷環境事件影響哪些節點時用子字串比對 | [trigger.py:219](../src/core/monitoring/trigger.py#L219) | "A1" 會誤中 "A12"，觸發不必要的重規劃 | 未處理 |
| **A-03** | 架構落差 | 步驟之間不能傳遞資料，也沒有把結果彙整回覆給使用者的步驟 | [planner.py:161](../src/core/intent/planner.py#L161) | 多步驟任務無法串接，例如「推薦展品」的結果傳不到「規劃路線」 | 未處理 |
| **A-04** | 架構落差 | 同一個意圖被 LLM 拆兩次：`parse_intent` 的結果只用來比對動作，planner 又重新拆一次 | [intentional_agent.py:226](../src/core/intentional_agent.py#L226)、[intentional_agent.py:250](../src/core/intentional_agent.py#L250) | 兩次結果可能不一致，成本也加倍 | 未處理 |
| **P-01** | 效能 | `select_actions` 不帶 slots 又比對一次，而且每個動作都再查一次 KG（N+1 查詢） | [action_selector.py:59](../src/core/intent/action_selector.py#L59)、[action_selector.py:32](../src/core/intent/action_selector.py#L32) | 規劃延遲變長，embedding 費用增加 | 未處理 |
| **P-02** | 效能 | 每次比對都執行 `SHOW INDEXES` 加 `awaitIndex`；讀不到維度時還會 DROP 索引再重建 | [action_store.py:93](../src/kg/action_store.py#L93)、[action_matcher.py:235](../src/core/intent/action_matcher.py#L235) | 多了往返延遲，正式環境中索引可能被反覆重建 | 未處理 |
| **P-03** | 效能 | 黑板每 2 秒輪詢一次，做全量比對 | [watcher.py:49](../src/blackboard/watcher.py#L49) | 有延遲，資料量大時無法擴充 | 未處理 |
| **G-01** | 通用性 | 只寫死了 info、navigation 兩個頻道，未知 topic 一律送往 info | [_executor_utils.py:31](../src/agents/_executor_utils.py#L31) | 新增一種執行端 Agent 就要改核心程式 | 未處理 |
| **G-02** | 通用性 | 觸發條件只看 `Zone/`、`Booth/` 開頭的主題 | [trigger.py:38](../src/core/monitoring/trigger.py#L38) | 綁死展場領域，「通用」的主張打折 | 未處理 |
| **S-02** | 安全 | MQTT 用明文帳密，沒有 TLS 或 ACL | `gias.toml` | 訊息可被竊聽或偽造 | 未處理 |
| **S-03** | 安全 | OpenAI API 金鑰以明文存在 `gias.toml`（已在 .gitignore、沒被 git 追蹤） | `gias.toml` | 本機或備份檔（`_bak/*.7z`）外流時金鑰會一起外洩 | 未處理 |

## 四、低（11 項）

| 編號 | 類別 | 問題 | 位置 | 狀態 |
|---|---|---|---|---|
| **L-05** | 閉環／監測 | 進度訊息只寫進 log，沒有用在監測判斷 | [monitor.py:84](../src/core/monitoring/monitor.py#L84) | 未處理 |
| **C-09** | 正確性 | `BudgetGuard.exhausted()` 只看 deadline，和它的註解、文件描述不符 | [budget.py:73](../src/core/monitoring/budget.py#L73) | 未處理 |
| **P-04** | 效能 | 沒有 embedding 快取（`llm/cache.py` 已存在但沒被使用） | [cache.py](../src/llm/cache.py) | 未處理 |
| **P-05** | 效能 | Planner 遞迴時固定 `sleep(0.1)`，子意圖逐一處理、沒有平行化 | [planner.py:183](../src/core/intent/planner.py#L183) | 未處理 |
| **G-03** | 通用性 | BlackboardAgent 裡寫死了展場專用的寫入邏輯（Zone/Booth） | [blackboard/agent.py:246](../src/blackboard/agent.py#L246) | 未處理 |
| **S-04** | 安全 | 使用者輸入直接嵌入 prompt，沒有 prompt injection 防護 | [prompt_builder.py:12](../src/core/intent/prompt_builder.py#L12) | 未處理 |
| **E-02** | 工程品質 | 用 `_bak/*.7z` 備份取代 git 分支；commit 訊息有 ".."、"aaa" 這類不具描述性的寫法 | `_bak/` | 未處理 |
| **E-03** | 工程品質 | 有空殼或沒被使用的模組：`llm/cache`、`router`、`policy`、`observability`，`schemas/plan.py`、`eval.py`，`tasks/planning_tasks.py`、`evaluation_tasks.py`，`kg/client.py` | `src/llm/`、`src/kg/` | 未處理 |
| **E-04** | 工程品質 | `print` 和 logger 混用，log 層級無法統一控制 | `planner.py`、`src/agents/*` | 未處理 |
| **E-05** | 工程品質 | OpenAI 聊天模型寫死成 `gpt-4.1-mini`，無法從 toml 設定 | [openai_provider.py:41](../src/llm/providers/openai_provider.py#L41) | 未處理 |
| **E-06** | 工程品質 | 沒有 CI（`.github/` 是空的），548 個離線測試沒有自動執行 | `.github/` | 未處理 |

---

## 建議處理順序

1. **先修小而明確的 bug：** C-01、C-02、C-03、C-04，每項都能在一個檔案內修好並補回歸測試。
2. **接通閉環：** L-01 → L-02 → L-03。
3. **補實驗：** 先做 V-01、V-02（導航實驗加上真的跑 GIAS 的 `--live` 模式），再補 V-05 的消融實驗程式。
4. **補論文核心機制：** A-01、A-02。
5. **安全與工程：** S-01、E-01 盡早處理，其餘可以慢慢清。

---

## 修正紀錄

### 2026-10-09：C-01 ～ C-04（未 commit）

| 編號 | 修正內容 | 主要檔案 |
|---|---|---|
| C-01 | Planner 把 LLM 的同層 id 轉成階層式全域 id（父 "2" 的子節點 "1" → "2.1"），relationships 同步改寫；同層重複或缺 id 時自動補成唯一。PlanCursor 遇到重複 id 改為拋出 `DuplicateNodeIdError`（`replace_subtree` 回傳 False、`replace_root` 拋錯，且都不改動 cursor）；`plan_intention` 與 `execute_plan_with_monitoring` 會拒絕 id 重複的 plan | `planner.py`、`cursor.py`、`repair.py`、`intentional_agent.py` |
| C-02 | `plan_intention` 新增規劃驗證：沒有 task 的 atomic、沒有子節點的 composite 一律判為無法執行（`leaf_unresolved`，原因列在 `debug.unexecutable_nodes`）。兩條執行路徑派工前再檢查一次，不送出、直接標為失敗。InfoAgent／NavigationAgent 收到缺少 task 的請求改回 `ok=False` | `intentional_agent.py`、`_executor_utils.py`、`info_agent.py`、`navigation_agent.py` |
| C-03 | ScopeGate 在 LLM 失敗或回覆格式不符時拋出 `ScopeGateError`，交由 `scope_gate_strict` 決定拒絕或放行；`can_execute` 改為嚴格解析（原本 `bool("false")` 會變成 True） | `scope_gate.py` |
| C-04 | 新增節點逾時：`Budget.node_timeout_sec`（預設 30 秒，設定鍵 `intent.monitoring.node_timeout_sec`，≤ 0 表示不限），節點自身的 `deadline_sec` 優先。逾時節點會送 cancel，並當成失敗結果交給 trigger 走 retry / replan。順帶修正：retry 前舊派工遲到的結果原本會覆寫新一次派工的狀態，現在會被忽略 | `budget.py`、`cursor.py`、`intentional_agent.py`、`docs/monitoring_protocol.md` |

新增回歸測試 37 個（`tests/intent/`、`tests/monitoring/test_cursor.py`、`tests/test_execute_plan_with_monitoring.py`、`tests/test_execute_plan.py`、`tests/agents/`）；離線測試共 606 個全數通過。

另外，`tests/test_plan_intention_policy.py` 原本用「沒有任何步驟的空計畫」當作規劃成功的案例，C-02 修正後會被正確判為無法執行，因此把該測試的假計畫改成含一個可執行步驟。

### 2026-10-09：L-01 ～ L-03（未 commit）

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

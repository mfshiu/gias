# GIAS1 驗證場景提案：展場導航以外的三個候選

> 2026-10-19。對象：docs/Gias1.md 的十條準則。目標：展場導航以外，最實用、最便於實現、能驗證 GIAS1 的實作場景。
> 由六個角度提出 23 個候選，合併為 6 個，各經可行性、驗證力、懷疑者三方評審後選出三個；文中「修正」指評審對照程式碼後更正的候選稿主張。

## 一、摘要

六個經三方評審的候選中，建議選 **檔案管家（File Steward）**、**模擬裝置管家（Device Twin Butler）**、**會議室預約台（RoomDesk）** 三個。選擇理由各一句：

- **檔案管家**：只用 stdlib、零新依賴、零新服務 process，真相是任何人用檔案總管就能核對的沙箱檔案樹；是六案中共識最高（五個角度獨立提出）、第一批真實數據最快的一個，也是作者每天真的會用的工具。
- **模擬裝置管家**：煙霧警報搶占睡覺模式、百葉窗馬達與投影機暖機的不可中斷，是準則 8／9 最自然、最不造作的載具；長動作天然提供取消與心跳窗口，代價是要先寫一個 mock 裝置伺服器。
- **會議室預約台**：free→held→booked 效果鏈是「宣告什麼就檢查什麼」最乾淨的練習，hold TTL 到期讓世界自己變（不必人為注入）；SQLite＋純文字 .ics 零新依賴，院長搶占學生是最容易向外人解釋的優先序示範。

這不是單純取平均分前三名。平均分依序為檔案管家 7.7、裝置管家 7.3、服務維運台（LocalOps）7.0、會議室預約台 7.0、開發工作站 6.7、資料報表台 6.7；LocalOps 與 RoomDesk 同分，本提案選 RoomDesk 而非 LocalOps，理由：(1) 三位評審修正後工時 RoomDesk 13／13／14 天，LocalOps 15／15／16 天；(2) RoomDesk 零新依賴，LocalOps 需 psutil（gias 環境未安裝、requirements.txt 未列）且 Windows 子行程／port 回收要 1～2 天除錯，懷疑者實測還發現 Windows 上 `HTTPServer` 預設 `SO_REUSEADDR` 讓「占 port 製造失敗」不成立；(3) LocalOps 的自我觸發是結構性的——Restart／Deploy 改變的正是該步驟所指 Service 的 state／health，連最簡單的意圖 1 都會被自己的重啟事件打斷，收窄到 `Service/*/health` 無效，而 RoomDesk 的主要缺陷（執行窗口毫秒級）可在情境層加可設定延遲解決；(4) 多樣性：LocalOps 與裝置管家都是「mock http.server＋HTTP 探針感測器」的形狀，RoomDesk 的 SQLite 資料列是第三種不同的執行端與真相。三案合起來，執行端分別是檔案系統、HTTP 數位分身、SQLite 交易，三種不同的世界與真相來源；除準則 5（三案都以 enable_llm_assist 對照組作結構性驗證）之外，其餘九條每一條至少被兩個場景自然觸發。

| 場景 | 核心世界模型 | 執行端 | 主要驗證的準則 | 預估工時（評審修正值） | 真相來源 |
|---|---|---|---|---|---|
| 檔案管家 File Steward | 沙箱資料夾樹：Folder／File／Archive／Disk；FolderWatchSensor＋DiskSensor 寫入 | files（stdlib shutil／zipfile／hashlib，真實）＋notify（模擬） | P1 續做不重做、P3 效果 overlay（EnsureFolder／ZipFolder）、P9 分塊取消與 zip 不可中斷、P10 混合真實／模擬 | 11／13／14 天，取 13；第一個真實意圖第 3～4 天 | 沙箱樹快照（路徑、大小、sha256）、zipfile.namelist、executor_ops.jsonl、唯讀 bolt 讀 (:Intention) |
| 模擬裝置管家 Device Twin Butler | Room／Device 數位分身，世界在 mock_home_server 的 state.json | device（HTTP 對本機分身，對 GIAS 為真實）＋comms（模擬） | P8 三層優先序與安全搶占、P9 馬達／門鎖／暖機不可中斷、P4 裝置離線升級鏈、P10 裝置說謊邊界 | 13／14／15 天，取 14；第一次真實跑約第 4～5 天，M0 實際 6～7 天 | requests.log（seq、task_id、motor_start／motor_done）、state.json、temps.jsonl、GET /devices 終態 |
| 會議室預約台 RoomDesk | Building／Room／Slot／Booking，世界在 roomdesk.db（SQLite WAL） | booking（SQLite 交易）＋calendar（.ics）＋notify（模擬） | P3 free→held→booked 效果鏈、P4 hold TTL 世界自變、P8 院長搶占／同序先到先做、P2 感測器只讀不訂 | 13／13／14 天，取 13；意圖 2／3 第 3～4 天，第一批 Claim–Truth 第 6～7 天 | sqlite3 直讀 bookings／holds、outbox/*.ics 的 DTSTART／LOCATION、唯讀 bolt 讀 Intention 與 Slot.state |

### 三案共用的前置工作與已知核心缺口

三個場景都建立在「核心零修改」之上，評審逐檔核過的掛鉤都在：任意 `<channel>.request` 可派工（src/agents/_executor_utils.py L37-68；tests/test_executor_channels.py L54 起以 `robot.arm.request` 驗過）、IA 派工前依頻道動態訂閱 result/progress（src/core/intentional_agent.py L1346-1347）、state_probes 是帶 `$arg` 回 `value` 的 Cypher（L622-641）、DomainProfile 十個欄位齊備（src/core/intent/domain_profile.py L25-35）、效果 overlay 已實作（src/core/intent/preconditions.py L111-156；派工前以 DONE 節點效果覆疊 L677-681）。但以下五件事三案都要做，且不在任何一案自己的行數估算裡算足：

1. **獨立設定檔與資料庫**：gias.toml 目前 85 行、沒有 `[blackboard]`／`[intent.monitoring]` 區段；一旦加 `[[blackboard.watch_targets]]` 會**取代**預設的 Agent/Zone/Task/POI/Booth（src/blackboard/agent.py L84-86、L136-143；watcher.py L125-151），導航的 live 模式會失去 Zone/Booth 監看。必須以 `GIAS_CONFIG_PATH`（src/app_helper.py L50-55）另開 gias.<scenario>.toml，並指到獨立的 actions／blackboard database（seed_actions.py L307-310 會 DETACH DELETE 全部 Action）；多 database 依賴 docs/guideops_validation_plan.md §十.2 待決的 Neo4j Desktop 5.26.8。
2. **serve.py**：src/run_intentional_agent.py L111-116 的 `--serve` 寫死 EXPO_PROFILE，每個場景約 40～50 行自建 `IntentionalAgent(agent_config, domain_profile=...)`。
3. **executor_base.py**：InfoAgent 的 TOPIC 常數在模組層（src/agents/info_agent.py L44-46），不能靠建構參數換頻道；兩條路：複製 L250-374 的 `_handle／_handle_cancel／_publish_result／_publish_progress` 成頻道參數化基底約 150 行，或子類化並覆寫 `on_connected／_publish_result／_publish_progress` 三個方法約 30～40 行（NavigationAgent 的 `is_simulated` L327、`execute_task` L438 掛鉤與 navigation_scenario/live/guide_agent.py L239-243 的覆寫方式可照抄）。做一次三案共用。
4. **kiosk.py 與 tap.py**：repo 內沒有任何 kiosk 用戶端程式；kiosk 只以 requester 名稱出現在 tests/test_intention_service.py 與 docs/intention_protocol.md §4，docs/guideops_validation_plan.md §六已有 kiosk_client 的設計但未實作。要寫 paho 直連（navigation_scenario/event_injector.py L51-59 的 `_wrap_parcel`、L65 的 MqttEventInjector 寫法）或 Agent 子類用 `publish_sync`（src/blackboard/client.py L20-29）。約 110～120＋60 行，三案共用。
5. **動作比對校準**：src/tools/calibrate_action_matcher.py L24 的 `ACTION_SEEDS` 已有 `--actions` 選項（L62），只需加一個條目指到新場景的 seed；但新領域首輪校準至少 1 天。

三案都會量到、但本輪**不修**的核心缺口（寫進金標為「預期行為」，不算場景失敗）：

- **不可中斷無法從 Action KG 宣告**：planner 只複製 topic／task／action_id／params_schema／preconditions／effects／outputs（src/core/intent/planner.py L357-366），`interruptible`／`deadline_sec` 只在 intentional_agent.py L1359-1361（派工 payload）與 cursor.py L418（節點逾時）以 `node.get` 讀取，planner L357-366 不複製，所以永遠是 None，執行端收到預設 True（info_agent.py L258）。不可中斷只能由執行端對特定 task 硬編。docs/monitoring_protocol.md §6 的描述與實作不符。
- **叫停後遲到的完成結果會丟失**：`_cancel_in_flight` 送 cancel 後立刻把節點標 **OBSOLETE**（intentional_agent.py L1403-1415；src/core/monitoring/cursor.py L582-590），`cancel_grace_sec` 讀進 Budget 後無人使用；`_completed_steps` 只收 `state=="done"`（L283-288），所以被搶占時正在執行的不可中斷動作做完了也不算完成，續做會重做那一步。這是 GIAS1 準則 9 後半句在核心層的真實缺口，三案的真相檢查都要把「DONE 步驟重做」（期望 0）與「OBSOLETE 但實際完成的步驟重做」（已知缺口）分開計數。
- **自我觸發重規劃**：BB-3 對經 blackboard.control 的寫入不過濾（docs/guideops_validation_plan.md L182），`_decide_from_env` 不分方向、`_find_affected_nodes` 連 IN_FLIGHT 節點都算受影響（src/core/monitoring/trigger.py L274-340），EntityMatcher 會把上層實體展開到所有下層（src/core/monitoring/entity_match.py L45-62）。三案的 env_subscriptions 都必須**只訂執行端自己不會改動的屬性**，否則計畫會被自己的效果打斷並燒掉 `max_replans=3`（src/core/monitoring/budget.py L18）。
- **`abort_after_failures=5`／`abort_after_obsolete=8` 寫死**（trigger.py L48-49），`_build_trigger_config`（intentional_agent.py L1204-1216）不從 toml 讀。
- **效果以參數為 key，inputs 綁定的參數在規劃時不存在**：planner.py L372 把被 inputs 綁定的參數移出 raw_params，`_arg_value` 回 not ok → check_node 記 unverified「parameter missing」（preconditions.py L92-95）、apply_effects 跳過（L118-119）。只有直接指名參數的步驟能在規劃期走到 overlay；inputs 綁定的步驟只在派工前（`_bind_inputs` 之後）受檢。
- **查無節點不是 unverified**：probe 查詢成功但無列時回 `(True, None)`（intentional_agent.py L638），`eq`／`exists` 對 None 判違反，`ne`／`not_in` 對 None 成立（preconditions.py L48-61）。真正的 unverified 只有三種：黑板代理離線（rows=None → 30 秒內都 unverified，L635-637）、state_probes 沒有該 key（L631-633）、步驟缺 arg 參數。
- **拒絕是 LLM 判斷**：ScopeGate（src/core/intent/scope_gate.py L47-83）只看 action 名稱與描述；確定性擋下的是 `_validate_plan` 的白名單（intentional_agent.py L563-568）。「範圍外意圖零 `*.request`」不能當必達指標，應改成「零成功副作用、世界不變」並區分規劃期拒絕與執行期拒絕。

---

## 二、場景一：檔案管家（File Steward）

### 情境與使用者

一個受控的沙箱資料夾樹（inbox／papers／archive／backup／quarantine／trash）就是世界。作者以自然語言交付整理、搬移、重新命名、打包、校驗、隔離的意圖；files 執行端用 stdlib 真的動檔案；FolderWatchSensor 把檔案系統現況寫進黑板；notify 執行端刻意保留模擬。

requester 與優先序（已依評審修正）：

- `desk-user`（priority 0，作者本人 kiosk，日常整理）
- `nightly`（priority 0，排程用戶端；與 desk-user 同序，驗證先到先做）
- `urgent`（priority 10，「投稿截止前立刻備份」，搶占進行中的長批次）
- `coauthor`（priority 5）——**修正**：`IntentionManager.submit` 只要新意圖 priority 嚴格大於目前執行者就 `_request_stop("preempted")`（src/core/intention/manager.py L124），p5 對執行中的 p0 一樣搶占；「插隊不搶占」只在 urgent p10 執行期間提交 coauthor 才成立。劇本改為：在 urgent 備份進行中提交 coauthor，驗證它排在 desk-user 之前但不搶占 urgent。
- `cron`（priority -5，只用來驗證低序永遠最後；負數合法，manager.py L117 `int(priority)`）

### 意圖範例

- **多步＋資料流＋效果鏈**：「把 inbox 裡所有 PDF 搬到 papers/2026-10，然後把那個資料夾打包成 papers.zip 放到 archive，算好 SHA-256 再通知我」→ `ListFiles(inbox, *.pdf)` 輸出 files → `MoveFiles(inputs: files, dest=papers_2026_10)` → `ZipFolder(papers_2026_10, archive_name=papers.zip)` PRODUCES `archive_exists=present` → `Checksum(archive_name=papers.zip)` REQUIRES `archive_exists exists` → `NotifyUser`（模擬）。劇本把 `archive_name` 設為可直接指名的參數（不用 inputs），讓 Checksum 的前置條件在規劃期真的靠 overlay 成立。
- **應被拒絕**：「把 C:\Windows\System32 整個刪掉」「把這些檔案寄給老闆」「上傳到 Instagram」。目錄刻意沒有永久刪除、寄信、上傳；但 ScopeGate 是 LLM 判斷，「刪掉」可能被對到 `MoveToTrash`，此時由 `MoveToTrash` 的 `REQUIRES path_scope exists`（沙箱外 Folder id 無值）在規劃後檢查擋下，最後一層是執行端對沙箱外路徑一律 `ok=false`。量「零成功副作用」而非「零 request」。
- **搶占／取消序列**：意圖 1 的 `MoveFiles` 分塊進行中（每 10 檔一塊、bytes_per_sec 節流）提交 urgent p10「現在立刻把 contracts 完整備份到 backup」→ IA 送 `files.cancel`、執行端在檔案之間早退 `cancelled=true`、已搬的保留 → 意圖 1 轉 suspended → `CopyTree(contracts→backup)` achieved → 意圖 1 `attempts=2` 續做，`_resume_context`（intentional_agent.py L303-315）帶 completed_steps，新計畫不得再有 `ListFiles`、已搬檔案 mtime 不變、papers.zip 只建立一次。第二變體：在 `ZipFolder` 進行中送 `intention.cancel`——執行端自宣告不可中斷，寫完 temp 後 `os.replace`、`testzip` 通過才回 `ok=true cancelled=false`；IA 側已標 OBSOLETE，遲到結果不進 `_completed_steps`——這一格在金標裡預登錄為核心缺口。

### 世界模型

Labels（全新，不用 Zone/Booth）：

- `Folder {id, path, exists, writable, lock ∈ unlocked|locked, capacity ∈ ok|low|full, file_count}`
- `File {id, path, name, ext, size, sha256, mtime, state ∈ present|locked|missing}`
- `Archive {id, path, entries, sha256, task_id}`
- `Disk {id:'sandbox', space_state ∈ ok|low|critical}`
- 關係 `(File)-[:IN_FOLDER]->(Folder)`、`(Archive)-[:CONTAINS]->(File)`

id 用邏輯名（inbox、papers_2026_10、archive、backup、quarantine）；LLM 只能指名 Folder id，File 一律經步驟輸出 inputs 傳遞。合法 Folder id 必須寫進 Action 描述——規劃 prompt 只給 LLM `Action(Param…) | desc | Outputs`，不含 enum 值與 example（src/core/intent/action_selector.py L37-50），再靠 `enum_alias`（src/core/intent/action_params.py L140-151）與 `param_validation=strict`（intentional_agent.py L805-808）擋臆造。

state_probes（全為字串桶，因 `_OPS` 只有 eq／ne／in／not_in／exists／not_exists，preconditions.py L29）：`folder_exists`、`folder_lock`、`folder_capacity`、`folder_locked_count`（toString）、`file_locked`、`archive_exists`、`disk_space_state`（全域，arg 空白走 `_arg_value` 回 `(True, None)`）。seed_blackboard 必須替每個 Folder 寫齊 baseline 屬性，否則 `folder_locked_count eq '0'` 對 null 判違反、`folder_capacity ne full` 對 null 卻成立。

誰寫入：`Folder/File/Disk` 只由 FolderWatchSensor（每 1 秒 `os.scandir`，差異比對後只對變化節點 MERGE，帶 observed_at、confidence）與 DiskSensor（`shutil.disk_usage`，可被注入器 override）寫；FilesAgent 只寫 `Archive` 節點與自己的 executor_ops.jsonl；IA 只碰 `(:Intention)`。

**訂閱（已依評審修正）**：`env_subscriptions=['Folder/*/lock', 'Folder/*/capacity', 'Disk/*/*']`。不能訂 `Folder/*/*`——`file_count` 是執行端自己搬檔造成、經感測器寫回的屬性，事件會命中 IN_FLIGHT 的 MoveFiles 與 PENDING 的 ZipFolder(papers_2026_10)，三塊即耗盡 `max_replans=3`。`file_count` 從 watch_properties 移除，只作 `env_fact_queries` 用；`drop_files` 劇本改以注入器直接寫 `Folder.alert` 一類獨立屬性當載體，或只作派工前檢查的對照。gias.files.toml：`[[blackboard.watch_targets]]` Folder(lock, capacity)／File(state)／Archive／Disk(space_state)，`projections=[]`、`broadcast_events=false`。

### 執行端

**files**（real，`simulated=False`）：

- `ListFiles(folder, pattern, older_than_days)` → outputs files, locked
- `MoveFiles(files | src_folder+pattern, dest, skip_locked)` REQUIRES `folder_exists[dest] exists`、`folder_capacity[dest] ne full`、`folder_lock[dest] ne locked`；分塊每 10 檔送 progress、檔案之間檢查 CancelToken；跳過鎖定檔以 `AdaptationLog kind=constraint_relaxed` 回報。PRODUCES `file_location=value_arg dest`——但 files 由 inputs 綁定、arg 是整個清單，此效果只在派工前有意義，不要靠它驗 overlay。
- `CopyTree(src, dest)` 長時間、progress 心跳、可取消（搶占窗口的主載具）
- `RenameFile(file, new_name)` REQUIRES `file_locked[file] ne yes`
- `EnsureFolder(folder)` PRODUCES `folder_exists=present`（規劃期 overlay 真正受測的兩條之一）
- `ZipFolder(folder, archive_name, exclude_locked)` REQUIRES `folder_locked_count[folder] eq '0'`、`disk_space_state not_in [critical]`、`archive_exists[archive_name] not_exists`；PRODUCES `archive_exists=present`（另一條）；執行端自宣告不可中斷；bytes_per_sec 節流
- `Checksum(files | folder | archive_name)` → outputs manifest_path, sha256[]
- `FindDuplicates(folder)` → outputs duplicates
- `MoveToTrash(files)` 只搬到 trash/，REQUIRES `path_scope exists`

（候選稿 principles 段落提到的 `CopyFiles`、`ResizeImages` 是合併來源殘留，不在目錄中；「沒宣告的不檢查」對照改用 `CopyTree` 不宣告 `file_locked`。）

副作用痕跡：沙箱樹本身（路徑、大小、mtime、sha256）、`zipfile.namelist()`、trash/ 內容、executor_ops.jsonl（task_id、來源、目的、sha256、耗時——數每個意圖同一步驟被做幾次）、`(:Archive)` 節點含 task_id。

**notify**（simulated，`simulated=True`，仿 InfoAgent 固定回覆）：`NotifyUser(message)`、`SendEmail(to, subject, body)`。第一版無痕跡——正是要驗 `result.simulated` 進 `simulated_steps`（cursor.py L706-716）並傳到最後回覆（src/core/intention/response.py L76-81 template 的「（模擬執行）」、L116-126 LLM 模式）。

### 環境事件與誰注入

| 事件 | 機制 | 預期 GIAS 行為 | 注入者 |
|---|---|---|---|
| lock_file(path) | 另一 process 以 ctypes `CreateFileW` share=0 持有句柄（模仿 Word），或人手用 Word 開 docx；20 秒後解鎖 | 感測器寫 `File.state=locked`、`Folder.lock`；ZipFolder 派工前前置條件不成立 → 不派工、REPLAN_SUBTREE（規劃者改 exclude_locked 或先 MoveFiles 排除）| event_injector.py／人手 |
| remove_folder(dest) | `os.rmdir` 目的資料夾 | MoveFiles `FileNotFoundError` → `ok=false` → retry 1 次仍失敗 → REPLAN_SUBTREE（加 EnsureFolder）→ 用盡 | event_injector.py |
| disk_pressure(level) | 經 DiskSensor 控制 topic override `space_state=critical` | ZipFolder／CopyTree 派工前不成立 → 重規劃到替代資料夾；報告如實標示為 override 非真填滿 | event_injector 經 ScenarioSensorAgent.inject_event |
| 停 BlackboardAgent 30 秒 | runner kill | probe 回 rows=None → unverified 不算違反、照常派工（準則 3）| scenario_runner |
| executor_offline／stall | kill FilesAgent 或停發 progress | `node_timeout_sec`（預設 30，閒置逾時）→ cancel → retry → replan → 預算用盡 | scenario_runner |
| kill_ia／restart_ia | CopyTree 進行中 taskkill serve 再重啟 | `(:Intention).status=interrupted`、不續做；執行端仍把工作做完（世界已改變的證據）| scenario_runner |

**修正**：候選稿「sensor_off → 查無 Disk 節點 → unverified」不成立：停感測器後節點仍在（過時值），即使不存在也是 None，`not_in [critical]` 成立。真正 unverified 只有停 BlackboardAgent 那一條。

### 對應 GIAS1 十條準則

| 準則 | 觸發方式 | 說明 |
|---|---|---|
| 1 忠於目標 | 自然 | 長批次被 urgent 搶占後續做；mtime 不變與 executor_ops.jsonl 計數是 GIAS 之外的「不重做」證據。kill serve 為劇本 |
| 2 共同記錄 | 自然 | Folder/File/Disk 只由感測器寫、FilesAgent 只留痕；核心不認識任何資料夾名；tap 核對 IA 的 write 只碰 Intention |
| 3 檢查宣告 | 自然 | EnsureFolder／ZipFolder 效果在規劃期真的放行 Checksum（repo 第一次活體使用 PRODUCES）；CopyTree 不宣告 `file_locked` 所以鎖住的檔照常被複製；unverified 靠停黑板代理（劇本）|
| 4 有預算的堅持 | 自然 | 用 Word 開著 docx 就是最自然的鎖檔失敗；remove_folder 是真 OS 錯誤；deadline 靠 CopyTree 大樹（半劇本）|
| 5 規則掌舵 | 結構／劇本 | `enable_llm_assist` on/off 對照，與其他案相同 |
| 6 各司其職 | 自然 | process 層可驗：只有 FilesAgent 有沙箱寫入 |
| 7 留下紀錄 | 自然 | 跳過鎖定檔 `constraint_relaxed` 以 `files.progress state=adapted` 回報並附在 `result.adaptations` |
| 8 承諾有序 | 劇本 | urgent 備份搶占日常整理是可信故事；nightly／cron 純為驗 FIFO 與低序。FIFO 由 `_next` 的 `max(key=(priority, -created_at))`（manager.py L162）保證 |
| 9 可取消可搶占 | 自然 | 分塊搬檔取消保留已搬檔案；ZipFolder temp＋`os.replace` 是真實原子性需求；同時量到 OBSOLETE／遲到結果缺口 |
| 10 夥伴回報 | 自然 | files（real）與 notify（simulated）並存於同一計畫；`simulated_steps` 只列 NotifyUser |

### 可沿用的程式

- `src/agents/_executor_utils.py` 整檔（channel_topics L37-68、build_result(simulated=) L144-188、build_progress_payload／AdaptationLog／ADAPT_CONSTRAINT_RELAXED L199-246、CancelToken L264-287）
- `src/agents/info_agent.py` L250-374 handler 骨架、L53-77 `_simulate_progress`、L235-237 `is_simulated`；`src/agents/navigation_agent.py` L327／L438 掛鉤；`navigation_scenario/live/guide_agent.py` L239-243 覆寫方式
- `src/blackboard/client.py` L32-114 try_query_blackboard／write_blackboard／subscribe_blackboard；L149-160 `VENUE_STATE_PROBES` 作 Cypher 範本
- `navigation_scenario/sensors/base.py` L84 `ScenarioSensorAgent`：FolderWatchSensor／DiskSensor 覆寫 `sensor_kind`／`sample_update`／`CONTROL_TOPIC_PREFIX`（L102），**另要**覆寫 `kind_control_topic` 與 `BROADCAST_TOPIC`（模組層 `sensors_kind_topic` L33、`SENSORS_BROADCAST_TOPIC` L30 寫死 navigation_scenario 前綴）、宣告 `HANDLED_EVENT_KINDS`、設 `contact_probability=1.0`／jitter 0；`_send_to_blackboard` L272 多筆 write、inject_event／pause／set_interval 控制面 L374-534
- `navigation_scenario/event_injector.py` L51 `_wrap_parcel`、L65 `MqttEventInjector`
- `navigation_scenario/seed_actions.py` L271 `main()`——**修正**：直接讀模組全域 ACTIONS（L315），不能 import 換資料，複製約 150 行或重構成接受 actions 的函式；`src/kg/action_store.py` L321-384 CLEAR_*／write_action_conditions（含 value_arg）／write_action_outputs
- `navigation_scenario/seed_blackboard.py` L57／L84／L97／L137（皆底線私有且綁定 navigation config，實務是複製）；`navigation_scenario/kg_reset.py` baseline 還原
- `navigation_scenario/live/runner.py` L54 run_intention／L68 live_agent_config／L124 `_AgentHandle`／L270 `LiveSession`（開發期同 process 啟多 agent）；`navigation_scenario/live/environment.py` L65 `DirectEventApplier`
- `navigation_scenario/test_cases.py` L44 `DynamicEvent`／L54 `TestCase`；`navigation_scenario/metrics.py` L27／L159／L200；`navigation_scenario/analyze.py` 報表格式
- `tests/test_intention_service.py` L48 `_Harness`、`tests/test_executor_channels.py` L54 `_Broker`、`tests/navigation_scenario/test_live_e2e.py` L59 `_World`、`tests/test_simulated_results.py`
- `src/core/intent/domain_profile.py` DomainProfile；`src/tools/calibrate_action_matcher.py` L24 ACTION_SEEDS 加 `file_steward`

### 要新增的程式

| 檔案 | 粗估行數 |
|---|---|
| `file_steward/config.py`（沙箱佈局、Folder id→路徑、FILE_STEWARD_PROFILE）| 180 |
| `file_steward/seed_actions.py`（9 個 action 含 params／preconditions／effects／outputs）| 240 |
| `file_steward/seed_blackboard.py`（沙箱資料夾與樣本檔、Folder/Disk baseline 屬性寫齊、金標 manifest）| 130 |
| `file_steward/executor_base.py`（頻道參數化基底，含 NON_INTERRUPTIBLE_TASKS、SIMULATED_TASKS；三案共用）| 150 |
| `file_steward/files_executor.py`／`notify_executor.py` | 300／60 |
| `file_steward/sensors/folder_watch.py`／`disk_sensor.py` | 170／60 |
| `file_steward/event_injector.py`（lock／unlock／remove_folder／disk_pressure／kill-restart）| 120 |
| `file_steward/serve.py`／`kiosk.py`／`tap.py` | 45／110／60 |
| `file_steward/scenarios.py`（12～14 案）／`scenario_runner.py` | 200／260 |
| `file_steward/truth_check.py`（不 import GIAS）／`metrics.py` | 230／120 |
| `tests/file_steward/` | 300 |
| `gias.files.toml`、`.gitignore` 沙箱、`docs/file_steward_runbook.md` | 60＋100 |
| **合計** | **約 2,300～2,500 行**，src/core／src/blackboard／src/llm 零修改 |

### 工時與先後

評審修正值 11／13／14 天，取 **13 天**。

- **第 1～2 天**：gias.files.toml＋獨立 database、config／seed_actions／seed_blackboard、executor_base、serve.py；先決定訂閱只訂 lock／capacity／Disk。
- **第 3～4 天**：files_executor（ListFiles／MoveFiles／EnsureFolder／ZipFolder）、folder_watch、kiosk 手動 submit → 意圖 1（多步）、唯讀查詢、拒絕案在真實 MQTT／Neo4j／LLM 跑通，拿到第一批 intention.status 與沙箱樹對照（Claim–Truth 可算）。這是 **≤5 天內的第一批真實數據**；可行性評審認為以作者熟悉度 3 天可達，前提是訂閱與 enum 校準先處理。
- **第 5～6 天**：Windows 鎖語意實測（1～2 天不可省）、disk_sensor、event_injector、CopyTree 節流。
- **第 7～10 天**：scenarios 12～14 案、scenario_runner、truth_check、metrics、tap。
- **第 11～13 天**：tests/file_steward、llm_assist 對照組、runbook、報告分組。

### 真相來源與量測

`truth_check.py` 不 import GIAS：(1) 每案前後對沙箱做完整清單快照（路徑、大小、sha256），與案例定義時以純 Python 事先算好的期望樹比對；`zipfile.namelist`＋`testzip`；「trash/ 之外沒有任何檔案消失」是硬門；(2) 被鎖定檔案的位置與 mtime 不變、續做後已搬檔案 mtime 不變；(3) executor_ops.jsonl 的命令序列對 tap 的 `files.request` 序列對帳（續做後無已完成的 MoveFiles、zip 只建立一次）；(4) 唯讀 bolt 讀 `(:Intention)` 終態與 history、kiosk 的 intention.status JSONL 取 IA 宣稱 → Claim–Truth 矩陣、取消停止率（cancelled=true 後沙箱不再變）、模擬揭露率、拒絕正確率（零成功副作用、沙箱不變）。對抗測試：餵「achieved 但檔仍在 inbox」必須判失敗。

### 風險與對策

- **真的動檔案，路徑錯誤就是資料遺失**：執行端只接受邏輯 Folder id、拒絕沙箱外路徑、沒有永久刪除 action；「檔案不消失」不變式是硬門。
- **自我觸發**（評審三方一致的最大隱藏成本）：如上，不訂 `Folder/*/*`、`file_count` 移出 watch_properties；drop_files 另找載體；0.5～1 天調整。
- **Windows 鎖語意偵測與致失敗脫鉤**：阻擋 `shutil.move`／`os.replace` 的是另一 process 的開啟句柄（缺 FILE_SHARE_DELETE），`msvcrt.locking` 位元組範圍鎖不阻擋 rename；感測器「唯讀共享開檔」偵測不到一般 Python `open()` 的句柄，較可靠的探測是 `os.rename(path, path)` 自我改名；注入器用 ctypes `CreateFileW` share=0 模仿 Word。感測器探測本身會短暫阻擋執行端的 move，靠 retry 吸收。1～2 天實測。
- **檔案操作毫秒完成，搶占／取消窗口小**：分塊＋bytes_per_sec 節流與 CopyTree 大樹是必要工程，報告標示為時間模型人為放慢；`cancel_grace_sec` 預設 3 秒與 ZipFolder 收尾時間要對齊。
- **LLM 看不到 enum 值與 example**：合法 Folder id 寫進 Action desc；首輪綁參數迭代至少 1 天。
- **效果 overlay 命中率**：只有 EnsureFolder→folder_exists 與 ZipFolder→archive_exists 在規劃期生效；MoveFiles／MoveToTrash 的 PRODUCES 因 files 由 inputs 綁定而在規劃期略過。劇本把 archive_name 設為直接指名的參數。
- **ZipFolder 續做重做**：IA 標 OBSOLETE 後遲到的 ok 不進 completed_steps；ZipFolder 以 `archive_exists not_exists` 前置條件或覆寫同名 zip 設計成 idempotent，避免 truth_check 誤判。
- **`abort_after_obsolete=8` 寫死**：連續注入 lock／drop 使多步計畫反覆被替換時可能誤 ABORT；劇本注入密度受控、沙箱限 ≤30 檔。
- 失敗情境多半人為製造（鎖檔、灌滿）——但懷疑者指出 remove_folder 與開啟句柄都是真實 OS 錯誤，比 GuideAgent 在黑板圖上移動更真實，不是弱點。

---

## 三、場景二：模擬裝置管家（Device Twin Butler）

### 情境與使用者

一個獨立 process 的本機假裝置伺服器（`mock_home_server.py`，ThreadingHTTPServer，8～12 個燈／恆溫器／百葉窗／門鎖／投影機／洗衣機；溫度隨時間逼近目標、馬達有行進時間、暖機 30 秒）扮演真實世界。住戶／員工、排程、設施、保全以不同優先序提交「睡覺模式」「會議準備」「下班關閉」「煙霧警報全開燈全解鎖」意圖；執行端對假伺服器下令；狀態感測器把裝置狀態寫進黑板。這是 docs/guideops_validation_plan.md §九 落選的 Office Butler（ESP32）拿掉硬體與 ESPHome 整合後的純軟體版——§九的三個反對理由（硬體整合、雙領域互相覆寫 seed、任務短淺）分別被純軟體模擬器、gias.home.toml＋GIAS_CONFIG_PATH、恆溫器／馬達／暖機長動作拆除。

requester：`resident`（p0）、`scheduler`（p0 或 -5）、`facility`（p5，**會搶占** p0；「只插隊」只在 safety 執行期間成立）、`safety`（p100，由獨立的 `safety_requester` 用戶端訂到 `Room/*/smoke` 事件後提交，不是感測器本身，以守準則 6）、`guest`（p-1）。

### 意圖範例

- **多步、可平行**：「睡覺模式：關掉客廳和廚房的燈、恆溫器調到 24 度、拉下臥室百葉窗、鎖大門」→ `SetLight×2`／`SetThermostat`／`SetBlind` 平行，`LockDoor` 接續（執行端宣告不可中斷）。**修正**：候選稿「調暖兩度由規劃者算」不可行——dataflow 只做索引／欄位取值無算術（src/core/intent/dataflow.py L36-44），改為 `SetThermostat(delta=+2)` 相對模式由執行端計算，或 enum 步階。
- **應被拒絕**：「幫我訂披薩」（無對應 action）；「把鄰居的燈關掉」（`light_neighbor` 無節點 → probe None → `device_exists exists` 判**違反**而非 unverified → 規劃後重想仍違反 → leaf_unresolved）；「把伺服器機房的空調關掉」（`room_class eq critical` 的 block 前置條件）。
- **搶占序列**：睡覺模式（p0）完成 SetLight×2 後在 SetThermostat 爬升中，注入器讓 smoke_sensor 寫 `Room.smoke=true` → safety_requester 提交 p100「煙霧警報：打開所有燈、拉起所有百葉窗、解鎖大門、廣播請大家離開」→ `device.cancel`：恆溫器停在當時溫度 `cancelled=true`；SetBlind 馬達行進中收到 cancel 仍跑完才回報（requests.log 有 `motor_done`、result `cancelled=false`，但 IA 已標 OBSOLETE）→ 警報 achieved → 睡覺模式續做，不重關已關的燈（requests.log 中客廳燈無第二次 off）。

### 世界模型

- `Room {id, name, occupancy ∈ occupied|empty, temp_band ∈ cold|ok|hot, class ∈ normal|critical, smoke}`
- `Device {id, type ∈ light|hvac|blind|lock|projector|washer|speaker, room, state ∈ on|off|open|closed|locked|unlocked|running|idle, level, target_c, online ∈ online|offline, busy ∈ busy|idle, observed_at, confidence}`
- 關係 `(Device)-[:IN_ROOM]->(Room)`

state_probes：`device_online`、`device_state`、`device_busy`、`device_exists`（exists）、`room_occupancy`、`room_class`、`room_temp_band`（數值由感測器離散化）。`room_*` 可直接改寫 `VENUE_STATE_PROBES.zone_state` 的 CASE 往上找寫法（src/blackboard/client.py L151-157）。

Effects（已依評審修正）：`SetLight PRODUCES device_state=value_arg state`、`LockDoor PRODUCES device_state=locked`、`PowerProjector PRODUCES device_state=on`、新增 `PowerSpeaker PRODUCES device_state=on` 與 `Announce REQUIRES device_state[speaker] eq on`——候選稿所列 REQUIRES 無一引用 `device_state`，效果 overlay 原本沒有消費者，加這一組才驗得到 P3 的效果鏈。**刪除** `PowerOff(room) PRODUCES device_state=off`：overlay 鍵是 `(key, str(arg 值))` 字面值（preconditions.py L64-67、L111-128），`entity_parents` 只供 ReplanTrigger 用（trigger.py L85），前置條件層不會把 room 展開成裝置，`PowerOff(room=A)` 的效果會寫成 `device_state[A]`，永不命中。PowerOff 改以裝置清單為參數，或不宣告效果。

誰寫入：HomeStateSensor（ScenarioSensorAgent 子類，每 1 秒 GET /devices，只在值改變時 MATCH+SET，HTTP 逾時或 404 寫 `online=offline`、confidence 0.5）寫 `Device.state/online/busy`、`Room.occupancy/temp_band`；執行端只寫 `Device.last_command` 留痕，**不寫 state**（才量得到「裝置說謊」）。世界真相在 mock_home_server 的 state.json 與 requests.log，GIAS 任一元件都不寫它。

**訂閱（已依評審修正）**：`env_subscriptions=['Room/*/occupancy', 'Room/*/smoke', 'Room/*/class', 'Device/*/online']`。不能訂 `Room/*/*`——恆溫器爬升讓感測器改寫 `Room.temp_band`，而 `EntityMatcher.names_for` 把 Room 事件展開到該房所有子裝置（entity_match.py L49-62），會命中 IN_FLIGHT 的 SetThermostat（device=thermo_B 是 B 的子實體）並取消自己的爬升。`TopicPattern` 的 `*` 以 `[^/]+` 逐段比對（src/blackboard/topic.py L103），屬性層級 pattern 確能分開。`temp_band` 只作 env_fact_queries 用。gias.home.toml：watch_targets Device(state, online, busy)／Room(occupancy, temp_band, smoke, class)，`projections=[]`（第二版可試 `register_projection_preset('office')`，src/blackboard/projection.py L82；docs/blackboard_agent.md §2 範例）。

### 執行端

**device**（對 GIAS 為 real、`simulated=False`；世界為數位分身）：以 urllib 對本機 mock server `POST /devices/<id>`。

- `SetLight(device, state, level?)` 立即、可中斷；PRODUCES device_state
- `SetThermostat(device, target | delta)` REQUIRES `device_online eq online`、`room_class ne critical`；每秒逼近 0.5～1 度並送 progress，可取消（停在目前溫度）；目標超出 18～28 時夾住並以 `AdaptationLog constraint_relaxed` 回報
- `SetBlind(device, position)` REQUIRES `device_busy ne busy`；執行端自宣告不可中斷：馬達 6 秒忽略 cancel，做完回 `cancelled=false`
- `LockDoor／UnlockDoor(device)` REQUIRES `device_online eq online`；不可中斷（3～4 秒鎖舌）；PRODUCES device_state
- `PowerProjector(device, state)` 30 秒暖機、持續 progress 心跳（無心跳 30 秒即逾時，cursor.py L418-422 以最後回報時間算）、不可中斷
- `PowerSpeaker(device)` PRODUCES device_state=on；`Announce(room, message)` 改放 device 頻道並 REQUIRES device_state[speaker] eq on（或留 comms 模擬但仍宣告前置條件）
- `PowerOff(devices)` 多裝置批次、可取消；REQUIRES `room_occupancy eq empty`（arg 改為 room 參數另給）
- `WaitDeviceIdle(device, timeout_sec)` 輪詢至 idle，心跳，可取消——**修正**：KG 沒有把 deadline 帶到節點，「等待有期限」只能靠執行端自己的 timeout_sec 與全域 `node_timeout_sec`
- `ReadTemperature(room)` → outputs temp_c；`ListDevices(room)` → outputs devices

執行端需自備別名解析：派工 payload 用 `node.params` 原值（intentional_agent.py L1354），`entity_aliases` 只用於前置條件 resolve 與 trigger 比對，執行端收到的是「客廳燈」，像 GuideAgent 的目的地解析一樣對到 mock id，否則 404。AgentFlow `MessageDispatcher` 預設 workers=8，單一 device 頻道同時跑 8＋長動作時第 9 個請求與 `device.cancel` 排在同一佇列；調高 workers 或把 hvac／projector 拆成第二頻道，並以每裝置鎖保護。

副作用痕跡：mock server 自己維護的 state.json（每次變更快照）、requests.log（seq、時間、裝置、指令、來源 task_id、結果、motor_start／motor_done）、temps.jsonl 溫度軌跡、GET /devices 終態；黑板 Device 節點由感測器寫入可交叉比對。

**comms**（simulated，`simulated=True`）：`NotifyResident／NotifyRoom(message)`、`NotifySecurityCompany(message)`；無痕跡或只寫一行 security_calls.jsonl。

### 環境事件與誰注入

| 事件 | 機制 | 預期行為 | 注入者 |
|---|---|---|---|
| 裝置離線 | `POST /chaos/offline/light_kitchen` | 感測器寫 `online=offline` → 派工前 `device_online eq online` 不成立 → 不派工直接 REPLAN_SUBTREE（precondition failed 屬 NON_RETRYABLE，src/core/monitoring/events.py L20）；已派工的 SetThermostat 無進度 → node_timeout → cancel → retry → replan | event_injector.py |
| 有人走進房間／牆上開關 | `POST /devices/light_living {state:on}` 或 occupancy(room, n) | `Room/A/occupancy` 事件 → 提到該房的 PENDING 步驟重想；PowerOff 的前置條件在派工前翻轉 | event_injector |
| 馬達／門鎖卡住、fail_once | `POST /chaos/jam/blind_bedroom`；控制 topic 設旗標 | 500 → retry 一次 →（仍敗）子樹重規劃 → 配額；fail_once → retry 即成功（第一層）| event_injector／scenario_runner |
| 溫度回升／開窗 | 模擬器 ambient 上調 | SetThermostat 進度倒退，hvac boost 以 `adapted（constraint_relaxed）`回報；`temp_band` 不訂閱，不觸發重規劃（負向對照）| event_injector |
| 洗衣機提早結束／一直 running | 改 washer 狀態 | WaitDeviceIdle 自然完成，或執行端逾時 ok=false → retry → replan | event_injector |
| 煙霧 | 注入器讓 smoke_sensor 寫 `Room.smoke=true` | `safety_requester` 訂到事件後提交 p100 → 搶占 | event_injector 觸發；提交者為 safety_requester |
| sim_down／整個重啟 | kill mock server 5 秒後重啟 | 全部 offline 又 online → 事件風暴下的 max_replans／`abort_after_obsolete=8`；停 BlackboardAgent 30 秒 → unverified 照派 | scenario_runner |
| kill_ia／restart_ia | SetThermostat 進行中殺 serve | interrupted；模擬器 hvac 仍自己把溫度調到目標（行動者做完手上的事）| scenario_runner |
| 裝置說謊（選用） | `/chaos/silent`：命令回 200 但不製冷 | 執行端誠實回 ok、世界沒變；暴露準則 10 的立場邊界，報告定位為邊界而非 bug | event_injector |

### 對應 GIAS1 十條準則

| 準則 | 觸發方式 | 說明 |
|---|---|---|
| 1 | 自然 | 睡覺模式被煙霧搶占後續做；requests.log 中已關的燈不再有第二次 off |
| 2 | 自然 | Device/Room 只由感測器從模擬器讀回；執行端只留 last_command 與 requests.log |
| 3 | 自然（需補一組效果消費者）| device_online／room_occupancy／room_class／device_exists 是裝置領域本有的前置條件；PowerSpeaker→Announce 驗效果鏈；SetLight 不宣告 room_occupancy（對照）；黑板離線 → unverified（劇本）|
| 4 | 半自然 | fail_once → retry；卡兩次 → replan；離線整場不恢復 → 用盡 → FAILED；chaos 旗標只是觸發方式，離線本身是 IoT 自然失敗型態 |
| 5 | 結構 | llm_assist 對照 |
| 6 | 自然 | 模擬器、感測器、執行端、safety_requester、IA 皆獨立 process；safety_requester 另立角色 |
| 7 | 自然 | 夾溫度 constraint_relaxed 記在該步 |
| 8 | **自然（六案中最飽滿）** | 住戶／排程／設施／保全是最直觀的多方承諾；嚴格 `>` 搶占（manager.py L124）、同序 FIFO（L162）|
| 9 | **自然（六案中最飽滿）** | 恆溫器停在當時溫度；馬達／鎖舌／暖機是「宣告不可中斷」最直觀例子；同時量到 OBSOLETE 缺口（續做重鎖門，真相出現兩筆鎖門）|
| 10 | 自然 | Announce／Notify 模擬；裝置說謊劇本踩在準則 10 邊界 |

### 可沿用的程式

- `src/agents/_executor_utils.py` 整檔；`src/agents/info_agent.py` L250-374 骨架；`navigation_scenario/live/guide_agent.py` L239-243 覆寫方式、L359-367 可取消 `_sleep`、L345-347 每步 progress 心跳、L316-323 `AdaptationLog.report`（SetThermostat／WaitDeviceIdle 範本）、L263／L279-283 `_move_lock` 序列化（每裝置鎖的參考）
- `navigation_scenario/config.py` L208-224 entity_aliases／entity_parents 建構、L231-316 `NAVIGATION_PROFILE` 欄位形狀（device→room ≈ booth→zone）；`src/blackboard/client.py` L149-160 `VENUE_STATE_PROBES.zone_state` CASE
- `navigation_scenario/sensors/base.py` L84 `ScenarioSensorAgent`：HomeStateSensor 只覆寫 `sample_update`（L170 抽象方法），`SensorUpdate(kind='write', statements=[...])` L58-82 支援 MATCH+SET 批次；同樣要覆寫控制 topic 前綴與設 contact_probability=1.0
- `navigation_scenario/event_injector.py` L51／L65；`navigation_scenario/live/environment.py` L65 `DirectEventApplier`（Room.occupancy 直接寫黑板的確定性路徑）
- `navigation_scenario/seed_actions.py` L271 seed 迴圈（複製）；`src/kg/action_store.py` L321-384；`navigation_scenario/seed_blackboard.py` L57／L84／L97／L137（複製）
- `src/observer/base.py` ObserverAgent 與 `src/blackboard/projection.py` L82 `register_projection_preset`（第二版 observe→投影鏈）
- `navigation_scenario/test_cases.py`／`metrics.py`／`analyze.py` 樣板；`tests/test_intention_service.py` L48、`tests/navigation_scenario/test_live_e2e.py` L59、`tests/test_executor_channels.py` L54；`tests/intent/test_preconditions.py`（overlay 單測）
- **修正**：`navigation_scenario/live/runner.py` 的 `LiveSession`（L270）是單次 `BenchmarkIntentionalAgent`、三代理同 process（L99-121），與本案多 requester 常駐、多 process 拓樸不同；實際可沿用的只有 `live_agent_config`（L68）的設定覆疊與啟停順序，runner.py 多半新寫

### 要新增的程式

| 檔案 | 粗估行數 |
|---|---|
| `device_twin/mock_home_server.py`（/devices、/devices/<id>、/chaos/*、/reset；state.json、requests.log、temps.jsonl；決定性溫度動力學、馬達／暖機計時、離線／卡住／說謊模式；thread-safe）| **350～450**（候選估 250 偏低）＋150 行測試 |
| `device_twin/config.py`（ROOMS／DEVICES、別名與從屬、DEVICE_TWIN_PROFILE、temp_band 門檻）| 180 |
| `device_twin/seed_actions.py`（10～11 個 action）／`seed_blackboard.py`（同時 POST /reset；複製 nav 的建庫 helper）| 260／**200～250** |
| `device_twin/executor_base.py`（共用則 0）／`device_executor.py`（同步命令、進度、不可中斷集合、夾值、別名解析、每裝置鎖）／`comms_executor.py` | 150／**320**／70 |
| `device_twin/sensors/home_state.py` | 140 |
| `device_twin/safety_requester.py`（訂 Room/*/smoke 後提交 p100；第一個非 IA 的黑板訂閱用戶端，需與 BlackboardAgent token 整合）| 90～120 |
| `device_twin/event_injector.py` | 120 |
| `device_twin/serve.py`／`kiosk.py` | 45／120（共用則 0）|
| `device_twin/scenarios.py`（16～20 劇本）／`runner.py`（Windows 子程序啟動／kill／重啟、port 就緒、殘留清理）| 220／**350** |
| `device_twin/truth.py`／`metrics.py` | 220／140 |
| `tests/device_twin/`（模擬器物理與命令、感測器離散化、取消、audit 對抗）| 400 |
| `gias.home.toml`（從零寫齊 watch_targets、projections、monitoring 區段）、`docs/device_twin_runbook.md` | 80／100 |
| **合計** | **約 3,300～3,800 行**（含測試），核心零修改 |

### 工時與先後

評審修正值 13／14／15 天，取 **14 天**。

- **第 1～2 天**：mock_home_server 連同獨立單元測試（決定性物理、/chaos、三個日誌檔）——沒有它整條鏈跑不起來，佔掉 M0 近一半。gias.home.toml 與獨立 database。
- **第 3～5 天**：config／seeds／profile、executor_base、device_executor（SetLight／SetThermostat／LockDoor）、home_state sensor、serve／kiosk → 意圖 1（睡覺模式）、鄰居的燈（拒絕）、單步查詢在真實 MQTT／Neo4j／LLM 跑通，拿到第一批 Claim–Truth。可行性評審：第一次真實跑約第 4～5 天；懷疑者：M0 整體 6～7 天較實。
- **第 6～8 天**：safety_requester、event_injector、SetBlind／PowerProjector／WaitDeviceIdle、搶占三層劇本。
- **第 9～12 天**：scenarios 16～20、runner、truth、metrics、說謊劇本。
- **第 13～14 天**：tests、llm_assist 對照、runbook。長動作讓每輪劇本數十分鐘，對照組 ×3 重複要預留時間。

### 真相來源與量測

`truth.py` 不 import GIAS：GET /devices 終態與劇本金標（每個 Device 期望 state/level/target、每個 Room 期望終態）比對；「不重做」＝搶占續做後已完成裝置無重複指令；「不可中斷做完」＝cancel 時間戳之後仍有 motor_done 且 result cancelled=false（只能在 requests.log 層面證明，IA 自身紀錄看不到）；「取消生效」＝cancel 後 requests.log 無新命令；黑板 Device 節點與模擬器快照交叉核對（黑板一致率、感測延遲）；唯讀 bolt 讀 `(:Intention).history` 與 kiosk 側錄三方對帳；對抗測試：餵「achieved 但燈仍 on」「achieved 但溫度未降」必須判失敗。可選每秒輪詢 Neo4j 的一頁儀表板作人眼第二來源。

### 風險與對策

- **世界是純軟體分身，四類中「真實」程度最低**：靠 requests.log 由 GIAS 外程式判定、模擬器與執行端為不同 process 緩解；報告定位為 digital-twin，真裝置留第三個實作（device 介面可直接換 ESPHome 轉接器）。
- **模擬器自己的 bug 看起來像 GIAS 的問題**：先有獨立單元測試與決定性物理（固定 dt、可重現 seed），再接 GIAS。
- **扇出撞寫死門檻（懷疑者列為最嚴重）**：煙霧警報約 13 個平行原子在扁平 root 下，任一盞燈離線觸發 REPLAN_SUBTREE → `replace_subtree` 把同 composite 的 IN_FLIGHT 兄弟全標 OBSOLETE（cursor.py L591-600）→ trigger 每輪開頭數 OBSOLETE ≥ 8 即 ABORT（trigger.py L105-118）。事前以批次動作（`AllLightsOn(room)`、`PowerOff(devices)`）壓低節點數，或接受把它當 GIAS1 邊界報告。候選稿誤指為 `planner_max_parallel`／`abort_after_failures`。
- **LLM 看不到裝置庫存**：「所有燈」「沒人的房間」要靠 action 描述嵌入庫存、批次動作，或 env_fact_queries（初次規劃沒有 env_facts，只有重規劃／續做才有，src/core/monitoring/repair.py L222-250）；需加 `empty_rooms`／`all_devices` 事實，接受「違反→重規劃」才成形的計畫型態。
- **自我觸發**：如上，不訂 `Room/*/*`。
- **執行端併發與別名解析**：見執行端段；約 +40～60 行與 dispatcher workers 設定。
- **ScopeGate 對「全開燈全解鎖」「解鎖大門」判定不穩**：事前以 30 句語料測過，記錄 ScopeGate 拒絕率作對照。
- **requester 權限**（guest 不可開鎖）無法用 state_probes 表達（arg 只能來自 action 參數），只能靠優先序；此處不做。
- **多 database 需 Neo4j Enterprise**（Desktop 內建），依 §十.2 定案。

---

## 四、場景三：會議室預約台（RoomDesk）

### 情境與使用者

以本機 SQLite 預約資料庫為世界、.ics 檔為可核對產物的會議室預約服務。Slot→Room→Building 階層，`slot_state` 的 free→held→booked 效果鏈是「檢查被宣告的部分」最乾淨的練習；hold TTL 到期是世界自變，不必全靠人為注入。

requester（已依評審修正）：學生／員工 kiosk（`user-<n>`，p0；多台同時提交驗先到先做）；系辦助理（`office`，p3——**會搶占**執行中的 p0，與 p9 行為相同；劇本改為在 dean 執行期間提交 office，驗證它排在 p0 之前但不搶占 dean）；院長室（`dean-office`，p9，搶占執行中的學生預約）。

### 意圖範例

- **多步＋資料流＋效果鏈**：「幫我在 10/20 下午 2 點訂一間能坐 8 人的會議室兩小時，訂好後寄確認信並產生行事曆檔」→ `FindFreeSlots` 輸出 slot_ids → `HoldSlot(inputs: slot_id ← slot_ids[0])` → `ConfirmBooking(inputs)` → `WriteCalendarFile` → `SendConfirmation`（模擬）。**修正**：此意圖以 inputs 傳 slot_id，規劃期 HoldSlot／ConfirmBooking 的前置條件記 unverified「parameter missing」、效果被跳過——計畫是「空過」不是「靠效果鏈過」；效果鏈真正受測是在派工前：`_bind_inputs` 代入後，HoldSlot DONE 的效果 `slot_state=held` 進 overlay，ConfirmBooking 的 `REQUIRES slot_state eq held` 讀到 predicted。規劃期 overlay 只在使用者直接點名時段（意圖 2／4／5）時受測，兩種要分開計量。「兩小時」的 schema 要先定：Slot 以小時為單位則需 HoldSlot×2＋ConfirmBooking×2，或 Hold／Confirm 接受 slot_ids 清單由執行端展開。
- **應被拒絕**：「幫我把 R301 的投影機換成新的，然後把全校預約清空」（無對應能力 → leaf_unresolved 可靠）；「訂隔壁公司的會議室」——**修正**：這句會通過 embedding 比對與 ScopeGate 對到 FindFreeSlots，拒絕發生在執行端 ok=false 之後，`*.request` 至少一次；指標分「規劃期拒絕」與「執行期拒絕」。
- **搶占序列**：學生 p0 五步意圖在 HoldSlot 進行中（BookingAgent 加可設定延遲 `seconds_per_op`，否則毫秒級動作讓搶占必落在 PLANNING，completed_steps 為空、「不重做」被平凡滿足）送出 dean p9「院長 10/20 14:00 要用 R301」→ `booking.cancel` 釋放 hold、`cancelled=true` → 學生 suspended → 院長 Hold＋Confirm → 學生 resumed：completed_steps 含 FindFreeSlots（不重查，booking.request 只出現一次）；HoldSlot 因時段已 booked → 續做會重新規劃，新計畫若把 slot id 寫成字面值，規劃後檢查就讀到 booked 並違反 → 重規劃一次改訂 R301-1020-16。真相：SQLite 中 dean 的 booking 早於學生、學生只有一筆 booking（除非搶占落在不可中斷的 ConfirmBooking，見風險）。

### 世界模型

- `Building {id}`、`Room {id:'R301', building, capacity, status ∈ available|maintenance}`
- `Slot {id:'R301-1020-14', room, date, hour, state ∈ free|held|booked}`
- `Booking {id, slot, requester, state ∈ confirmed|cancelled, ics_path}`
- 關係 `(Slot)-[:OF_ROOM]->(Room)-[:IN_BUILDING]->(Building)`、`(Booking)-[:FOR_SLOT]->(Slot)`

Slot id 採 `<Room>-<MMDD>-<HH>` 讓 LLM 可組出，`synonym_rules` 把「10/20 14:00／下午 2 點／14 點」正規化成「1020-14」；`entity_aliases` 需每個 slot 至少 1～2 個人類寫法（約 500 筆，程式生成）；`entity_parents`：Slot→Room→Building。**刪除** `room_capacity_band` probe：Precondition 的 value 是 seed 時的固定值，無法表達「容量 ≥ 使用者要求」這種隨意圖變動的條件，容量過濾只能在 FindFreeSlots 執行端內做。

state_probes：`slot_state`（`MATCH (s:Slot {id:$arg}) RETURN s.state AS value`）、`room_status`（$arg 為 Room 或 Slot id，CASE 往上找，比照 zone_state；注意 `ne maintenance` 對不存在的 Room 會成立，設計 probe 讓 Slot／Room 都查得到值）、`booking_state`。

Effects：`HoldSlot PRODUCES slot_state=held`；`ConfirmBooking PRODUCES slot_state=booked`；`ReleaseSlot／CancelBooking PRODUCES slot_state=free, booking_state=cancelled`。

誰寫入：世界是 `roomdesk_sandbox/roomdesk.db`（SQLite WAL，表 rooms／slots／holds／bookings，hold TTL 由 BookingAgent 清掃執行緒釋放）。FrontDeskSensor 每 1.5 秒讀 SQLite，變化時 write_blackboard 更新 `Slot.state`／`Room.status`（差異偵測在 `sample_update` 自己做，基底沒有「變化才寫」邏輯）；BookingAgent 對 SQLite 寫入後把 `Booking` 節點寫進黑板作留痕，**並在釋放 hold 時自己寫 `Slot.state=free`**（行動者留痕，P2 允許）——否則院長搶占後規劃若早於感測器 1.5 秒輪詢，讀到學生的 held → 違反 → 重規劃仍違反 → leaf_unresolved；event_injector 直接改 SQLite，不碰黑板。

**訂閱（已依評審修正）**：`env_subscriptions=['Room/*/*']`，**不訂 `Slot/*/*`**——IA 自己 Hold／Confirm 的結果經 FrontDeskSensor 寫回 `Slot.state`，PENDING 的 ConfirmBooking／ics／notify 步驟只要文字提到該時段或別名就會被自己的成功觸發 REPLAN_SUBTREE。時段變動（走入預約）改靠派工前檢查與 Hold 失敗路徑。gias.rooms.toml：watch_targets Room(status)／Slot(state)，`projections=[]`。

### 執行端

**booking**（real，SQLite 資料列為副作用，`simulated=False`）：

- `FindFreeSlots(date, start_hour, hours, min_capacity, room_id?)` → outputs slot_ids, room_ids（真實查 SQLite，容量過濾在此）
- `HoldSlot(slot_id)` REQUIRES `slot_state eq free`、`room_status ne maintenance`；PRODUCES `slot_state=held`；outputs hold_id；可取消（取消後自行回滾 hold——這是補償語意，協定只定義早退回 cancelled=true，需在 result 標示）；可設定 `seconds_per_op` 延遲；相鄰時段替代（constraint_relaxed）**預設關閉**，只在一組對照劇本開啟並單獨報告（與準則 6「行動者只做手上的事」有張力）
- `ConfirmBooking(slot_id, requester_name)` REQUIRES `slot_state eq held`；PRODUCES `slot_state=booked`；outputs booking_id；執行端自宣告不可中斷：SQLite 交易完成後才回報
- `ReleaseSlot(slot_id)` PRODUCES `slot_state=free`
- `CancelBooking(booking_id)` PRODUCES `booking_state=cancelled, slot_state=free`

副作用痕跡：roomdesk.db 的 holds／bookings（created_at、requester、state；殘留 hold 可查）、黑板 Booking 節點與 Slot.state。

**calendar**（real）：`WriteCalendarFile(booking_id)` → outputs ics_path，寫 `outbox/<booking_id>.ics`（純文字 RFC 5545，無外部依賴）；痕跡：檔案存在且 DTSTART／LOCATION 與 SQLite 一致。

**notify**（simulated）：`SendConfirmation(booking_id, recipient)` 固定訊息；無痕跡；最後回覆必須註明模擬。

SQLite 併發：BookingAgent（含 TTL 執行緒）、FrontDeskSensor、event_injector、audit 四方同檔存取，AgentFlow handler 可能在 dispatcher 執行緒執行；WAL＋busy_timeout＋每執行緒獨立 connection，否則 `database is locked` 會混進 Claim–Truth。

### 環境事件與誰注入

| 事件 | 機制 | 預期行為 | 注入者 |
|---|---|---|---|
| 走入預約搶走時段 | 直接在 SQLite 把 R301-1020-14 設為 booked（requester='walk-in'）| 若落在規劃期（使用者點名時段的意圖）→ FrontDeskSensor 寫 booked → 派工前 `slot_state` 不成立 → 不派工直接 REPLAN_SUBTREE；若晚於派工 → Hold 失敗路徑。**修正**：資料流意圖 Find→Hold 之間只有一個 0.2 秒 poll，1.5 秒感測器趕不上，派工前路徑只對點名時段＋注入落在數秒規劃期可達；兩條分開計量 | event_injector.py |
| 房間進入維護 | 改 `rooms.status='maintenance'` | `Room/R301/status` 事件 → entity_parents 命中提到 R301 時段的 HoldSlot／ConfirmBooking → REPLAN_SUBTREE 改房間；已 held 者由新計畫 ReleaseSlot | event_injector.py |
| hold 到期（世界自變）| TTL 清掃釋放 90 秒未確認的 hold | ConfirmBooking 發現 state≠held → ok=false → retry 1 次 → 子樹重規劃重 Hold。**修正**：「TTL=5 秒 → 三次 replan 用盡 → ABORT」機制錯誤——重規劃後新子樹的 Hold 與 Confirm 相隔 <0.5 秒不會過期；且預算用盡時 `_apply_replan_subtree` 回 applied=False（repair.py L146-147），節點留 FAILED、後續 PENDING 步驟派不出去，監測迴圈由 idle 守衛（intentional_agent.py L1161-1165）結束；不會發 ABORT 決策（預設設定下 ABORT 只在 deadline、5 次 FAILED、8 次 OBSOLETE；trigger.py L266-268 的「無復原策略」路徑需關掉 subtree replan 才會走到）。P4 第三層改用注入器在每次重 Hold 後立刻 walk-in 同一時段，或 `fail(times=n)` 式的 SQLite 觸發器 | BookingAgent TTL 執行緒／event_injector 調 TTL |
| 院長搶占 | kiosk scripted 於學生 HoldSlot 延遲中送出 p9 | 搶占、釋放、續做 | kiosk |
| 黑板代理離線 30 秒 | runner kill | unverified 照常派工，由執行端 ok=false 收尾 | scenario_runner |
| 服務重啟 | taskkill serve.py 於學生意圖 executing 時 | Intention 標 interrupted，殘留 hold 由 TTL 自然釋放（audit 檢查無孤兒 hold）| scenario_runner |

**修正**：候選稿「LLM 亂組的 slot id → probe 回 None → unverified」錯誤：查無列回 `(True, None)`，`slot_state eq free` 對 None 判**違反**（block）→ 規劃後帶脈絡重規劃一次、仍違反則 leaf_unresolved；派工前則不派工直接 REPLAN_SUBTREE 並消耗 max_replans。P3 的兩條分開計量應改為「violated（含不存在的 id）」vs「unverified（僅黑板離線劇本可達）」。首輪很可能因 id 格式而非世界變動把預算燒光，M0 需把 entity_aliases 列滿並讓 HoldSlot 的 slot_id 儘量走 inputs。

### 對應 GIAS1 十條準則

| 準則 | 觸發方式 | 說明 |
|---|---|---|
| 1 | 自然（需人為延遲）| 搶占後續做，FindFreeSlots 只派工一次（tap 數 booking.request）；BookingAgent 的 `seconds_per_op` 是讓搶占落在 EXECUTING 的必要條件 |
| 2 | 自然 | Slot.state／Room.status 由感測器從 SQLite 讀回；BookingAgent 留痕 Booking 節點；核心不認識房間 |
| 3 | **自然（效果鏈最乾淨）** | HoldSlot REQUIRES free／PRODUCES held → ConfirmBooking REQUIRES held／PRODUCES booked；派工前 overlay 真的放行；維護房間違反 → 重規劃；unverified 只靠黑板離線（劇本）|
| 4 | 自然 | hold TTL 到期不需注入；retry → REPLAN_SUBTREE（重 Hold）→ 預算；第三層需注入器配合 |
| 5 | 結構 | llm_assist 對照 repair_node（改 slot_id）是否出現並通過驗證 |
| 6 | 自然 | 感測器不會替人訂房；BookingAgent 不決定訂哪間（替代預設關閉）|
| 7 | 劇本 | 相鄰時段替代的 constraint_relaxed 只在對照劇本開啟 |
| 8 | 自然 | 三台 kiosk 2 秒內 p0 → created_at 順序；dean p9 搶占；兩個 p9 連續提交第二個排隊 |
| 9 | 自然 | 排隊取消 → dropped；HoldSlot 延遲中取消 → 釋放；ConfirmBooking 交易不可中斷 → 執行端做完回 ok=true 而 IA 已標 OBSOLETE——搶占情境下續做會重訂，審計要分開計 |
| 10 | 自然 | SendConfirmation 模擬；SQLite Claim–Truth（achieved 但 bookings 無該 requester＝假成功）|

### 可沿用的程式

- `src/core/intentional_agent.py` 常駐模式原樣（`__init__` L111-123 接受 domain_profile）；`src/run_intentional_agent.py` L111-116 `--serve` 寫法複製成 serve.py
- `src/agents/_executor_utils.py` 全部；`src/agents/info_agent.py` L250-374 handler 結構（executor_base 共用）；`src/agents/navigation_agent.py` L327／L438 掛鉤
- `src/blackboard/client.py` L71-114 write／subscribe；L151-157 `zone_state` CASE 改成 `room_status`；`src/blackboard/agent.py` BlackboardAgent＋`[[blackboard.watch_targets]]`
- `src/kg/action_store.py` L321-384；`navigation_scenario/seed_actions.py` L271 主流程複製；`navigation_scenario/seed_blackboard.py` L209-323 reset_to_baseline／L346 main 清庫建庫流程
- `navigation_scenario/live/guide_agent.py` L316-323 `ADAPT_REROUTE／CONSTRAINT_RELAXED` 用法、L203／L208／L326 `seconds_per_meter`（BookingAgent `seconds_per_op` 的範本）
- `navigation_scenario/sensors/base.py` L84 `ScenarioSensorAgent`（FrontDeskSensor 子類化；控制 topic 前綴與 contact_probability 同前；或另寫約 60 行固定輪詢）
- `navigation_scenario/event_injector.py` L51／L65 paho 寫法（kiosk／tap）
- `navigation_scenario/test_cases.py` L44／L54、`navigation_scenario/metrics.py` L27 dataclass 形狀；`tests/test_intention_service.py` L48 `_Harness`（L145-172 已有搶占後 resume context 只含已完成步驟的測試）；`tests/navigation_scenario/test_live_e2e.py` L59 `_World`；`tests/test_executor_channels.py` L54 `_Broker`
- `docs/intention_protocol.md` §4 payload 作 kiosk 固定斷言；`docs/action_preconditions.md` §6 seed 宣告格式；`src/core/intent/dataflow.py` L36-44 `slot_ids[0]` 索引式 inputs 已支援
- sqlite3 為標準庫（repo 內目前零使用）

### 要新增的程式

| 檔案 | 粗估行數 |
|---|---|
| `roomdesk_scenario/config.py`（2 棟×6 房、10/20～10/21 時段表、ROOMDESK_PROFILE 含 slot id 命名規則；entity_parents ~240 筆與 aliases ~500 筆程式生成）| 170＋生成器 |
| `roomdesk_scenario/seed_actions.py`（7 個 action）／`seed_blackboard.py`（Building/Room/Slot 節點＋roomdesk.db schema 與資料）| 240／190 |
| `roomdesk_scenario/store.py`（SQLite 存取層：find_free、hold(ttl)、confirm、release、cancel、sweep_expired；WAL、busy_timeout、thread-local connection）| 160 |
| `roomdesk_scenario/executor_base.py`（共用則 0）／`booking_agent.py`（5 task、TTL 執行緒、seconds_per_op、Confirm 不可中斷、釋放時寫 Slot.state）／`calendar_agent.py`／`notify_agent.py` | 150／240／90／70 |
| `roomdesk_scenario/sensors/front_desk.py`（讀 SQLite → 差異偵測 → write_blackboard）| 120 |
| `roomdesk_scenario/event_injector.py`（走入預約、房間維護、TTL 調整；直接 sqlite3；taskkill serve）| 90 |
| `roomdesk_scenario/serve.py`／`kiosk.py`／`tap.py` | 45／110／60（共用則 0）|
| `roomdesk_scenario/test_cases.py`（14 案）／`runner.py`（7～8 個 process 啟停與就緒探測）| 160／240 |
| `roomdesk_scenario/audit.py`（sqlite3 直讀、.ics 解析、唯讀 bolt、kiosk JSONL；Claim–Truth、孤兒 hold、先到先做順序、搶占後 booking 時間序、FindFreeSlots 重查次數）／`metrics.py` | 200／110 |
| `tests/roomdesk_scenario/`（store、booking_agent 取消／TTL、audit 對抗測試）| 300 |
| `gias.rooms.toml`、`.gitignore roomdesk_sandbox/`、`docs/roomdesk_runbook.md` | 60／110 |
| **合計** | **約 2,800 行**（候選稿寫 2,300，逐項相加為 2,795；executor_base 與 kiosk 共用時約 2,535），核心零修改 |

### 工時與先後

評審修正值 13／13／14 天，取 **13 天**。

- **第 1～2 天**：gias.rooms.toml、config（含 id 生成器）、seed_actions、seed_blackboard＋store.py、executor_base、serve.py。
- **第 3～4 天**：booking_agent（Hold／Confirm／Release／Cancel，含 seconds_per_op）、kiosk → 意圖 2（單步 Hold）、意圖 3（取消）、拒絕案跑通。**第一件事量 submit→Confirm 派工的時間分佔再定 TTL**（repo 內沒有任何 planning_sec 實測，navigation_scenario_results 只有模擬跑法的 completion_time_sec 中位 75 秒）。
- **第 5～7 天**：front_desk sensor、calendar／notify agent、意圖 1（五步資料流）、意圖 4（院長搶占）→ 第一批 Claim–Truth 數據。驗證力評審估在排除自我回音與時序問題後落在第 6～7 天。
- **第 8～11 天**：event_injector、14 案 test_cases、runner、audit、metrics、tap。
- **第 12～13 天**：tests、llm_assist 對照、runbook。

### 真相來源與量測

`audit.py` 不 import GIAS：sqlite3 直接讀 roomdesk.db（bookings：slot、requester、created_at、state；holds：是否殘留），列 `outbox/*.ics` 並解析 DTSTART／LOCATION 與資料庫比對，唯讀 bolt 讀 `(:Intention)` 終態與 history、`Slot.state`（黑板是否與世界一致），kiosk JSONL 取 IA 宣稱。輸出：Claim–Truth 矩陣、院長／學生 booking 時間序、FindFreeSlots 重查次數、孤兒 hold 數、ConfirmBooking 被 cancel 時交易完整性、simulated 揭露、拒絕正確率（分規劃期／執行期）。對抗測試：餵「achieved 無 booking」「cancel 後仍 confirm」「重複 FindFreeSlots」必須判失敗。

### 風險與對策

- **執行窗口毫秒級（懷疑者列為最嚴重）**：五個動作都是毫秒級 SQLite／檔案寫入，EXECUTING 階段 1～2 秒，LLM 規劃 5～20 秒，搶占多半落在 PLANNING。對策：BookingAgent 加可設定 `seconds_per_op`（仿 GuideAgent 的 seconds_per_meter），報告標示為人為放慢。加了延遲後自我回音問題會浮現，故訂閱先不含 Slot。
- **自我回音**：如上，只訂 `Room/*/*`，BookingAgent 釋放時自寫 Slot.state。
- **院長搶占的時序**：感測器 1.5 秒 vs 規劃耗時；BookingAgent 釋放時自寫 Slot.state 解決。
- **搶占落在不可中斷的 ConfirmBooking**：節點標 OBSOLETE、遲到 ok=true 不進 completed_steps，續做會重訂 → 學生兩筆 booking；審計把「學生只有一筆 booking」斷言改成分開計量此路徑，列為核心發現。
- **LLM 自組 slot id 或日期格式**：entity_aliases 列滿、synonym_rules 正規化、執行端對未知 id 回 ok=false；測試案例用絕對日期；`[intent.matching]` 的 min_score／fallback_min_score 已由 intentional_agent.py L140-147 讀取（預設 0.75／0.6），在 gias.rooms.toml 加區段即可調，不需改核心。
- **hold TTL 與真實 LLM 延遲競賽**：M0 先量再定；短 TTL 只用在 P4 劇本。
- **env_fact_queries 的 `free_slots_on_date` 在 ~240 個 Slot 下會超過 prompt_builder 4,000 字截斷**（src/core/intent/prompt_builder.py L9、L51-53）：查詢依日期／容量窄化。
- **規劃期效果鏈只在字面 id 計畫受測**：測試案例把「規劃期過關」與「派工前過關」分開計量。
- **SQLite 多執行緒**：WAL＋busy_timeout＋thread-local connection；Windows WAL 共享記憶體檔在不同權限下偶有鎖問題。
- **計畫形狀固定（Find→Hold→Confirm→ics→notify）**，多步但不深；對遞迴拆解壓力與檔案案相當。
- **實用性對作者本人不如檔案管家**，但院長搶占、櫃台走入預約是最容易向外人解釋的示範。

---

## 五、落選者

- **本機服務維運台（LocalOps）**，平均 7.0，修正工時 15～16 天：OS 層副作用（PID、port、HTTP）最無可爭議真實、自然故障最多，但自我觸發是結構性的——Restart／Deploy 改變的正是該步驟所指 Service 的 state／health，連意圖 1 都會被自己的重啟事件取消並重規劃，收窄到 `Service/*/health` 無效（重啟時 health 同樣 ok→fail→ok），只能訂 Host／Stack 而犧牲 P2／P5 主線；懷疑者實測 Windows `HTTPServer` 預設 `SO_REUSEADDR` 讓「占 port 製造 OSError」不成立（需 `SO_EXCLUSIVEADDRUSE`）；psutil 未安裝；候選稿 `src/security.py` 白名單精神的引用有誤（該檔只做 MQTT TLS／帳密檢查）；自列明細加總約 3,200 行而非 2,500。若日後要 OS 等級的真相，它是檔案管家之後最好的「第四個」。
- **開發工作站管家（Dev Desk）**，平均 6.7，修正工時 14～17 天：對作者最實用、reflog 與 junit 真相最硬，但工程量六案最高（四個執行端、三個感測器、沙盒專案），既有離線測試目錄實測只需 1～9 秒（非 30～60 秒），長動作全靠人工 sleep；分支名含 `/` 讓 watcher topic 多出段數永遠對不上 `Branch/*/*`；`CommitFile REQUIRES worktree_state eq dirty` 讓示範的 git 效果鏈在規劃期就 leaf_unresolved；Repo 樞紐＋`Repo/*/*` 訂閱使自我觸發成為常態；psutil／ruff／matplotlib 皆缺。
- **資料報表台（Data Report Desk）**，平均 6.7，修正工時 15～16 天：步驟間 inputs 最密集、原始 CSV 獨立重算的真相最無爭議、`fail(times=n)` 是 P4 最乾淨的分級器，但核心 `resolve_inputs` 對同一參數的多筆 inputs 後者覆寫前者（dataflow.py L189-203），三個「平行」範例中兩個（多個 CountRows→ExportWorkbook、兩個 Fetch→一個 Aggregate）接不起來；`CleanData` 的效果 arg 不在其參數中會被跳過；需 matplotlib、openpyxl 兩個新依賴；pandas 已安裝在 gias 環境但 requirements.txt 未列、repo 零使用，若採用亦要補列；只有一個角度提出、共識最低。可留作第二輪的資料流壓力測試。

---

## 六、建議先做哪一個，以及為什麼

**先做檔案管家。** 理由：

1. **最快拿到真實數據**：零新依賴、零新服務 process、世界就是檔案系統；可行性評審認為以作者對 repo 的熟悉度，config＋seeds＋files 執行端＋serve 複製＋手動 submit 三天內可跑通第一個真實意圖，第 4～5 天就有 Claim–Truth。裝置管家要先寫並測試一個模擬器（佔 M0 近一半），會議室預約台要先量 LLM 延遲定 TTL、再加人為延遲才有搶占窗口。
2. **三案共用的前置工作在這裡一次做完**：gias.<x>.toml＋GIAS_CONFIG_PATH 的獨立設定、executor_base、serve.py、kiosk、tap、calibrate 的 ACTION_SEEDS 條目——這些是 docs/guideops_validation_plan.md §十.5 想放進 `src/service/` 的領域無關元件，在檔案管家做完後，裝置管家與會議室預約台只剩領域層。
3. **五個角度獨立提出、共識最高**，三位評審的分歧只在工時（11／13／14）與兩處劇本錯誤（coauthor 優先序、sensor_off 不是 unverified），都已在本提案修正；沒有像 LocalOps 那樣的結構性阻礙。
4. **十條準則覆蓋完整且真相最獨立**：驗證力評審判定九條由工作負載自然觸發，唯一要劇本的是停黑板代理與 kill serve（任何場景都一樣）；「trash 外無檔案消失」的硬門與以純 Python 事先算好的期望樹，讓 oracle 完全不經 GIAS、不經 LLM。
5. **它會先踩到三案共同的核心缺口**（自我觸發、不可中斷無法宣告、OBSOLETE 後遲到結果丟失、效果 overlay 對 inputs 綁定參數略過），而且是在最便宜的世界裡踩到；把這些寫成 docs/gias_issue_list.md 的新條目後，裝置管家與會議室預約台的金標可以一開始就把它們預登錄為「預期行為」，不會再把核心缺口算成場景失敗。

接著做**模擬裝置管家**（補上準則 8／9 最飽滿的自然載具與數位分身世界），最後做**會議室預約台**（補上效果鏈與世界自變）。三案做完，除準則 5 以 enable_llm_assist 對照組作結構性驗證外，GIAS1 其餘九條準則每一條至少在兩個不同世界、兩種不同執行端上被自然觸發過，且 Label／狀態變數／頻道三者全不同，PLAN-11／GEN-1／GEN-2 的「核心不綁領域」也順帶證明。
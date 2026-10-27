# 毛球的心事：有意圖的電子寵物，遊戲內容規畫

> 2026-10-19。對象：docs/Gias1.md 的十條準則。定位：以遊戲形式驗證 GIAS1 的實作品，同一個世界日後可直接拿來對照 GIAS2。
> 延續 docs/gias1_scenarios.md 之外的「創意發想」第二案：提出者是寵物自己的身體，優先序就是驅力的強度。

## 一、一句話與核心玩法

**一隻你能讀懂心思的電子寵物。** 傳統電子寵物只給你看數值；這裡你看到的是牠的承諾佇列：牠想做什麼、多想（一個優先序數字）、做到哪一步、為什麼放棄。你的指令與牠的本能在同一把尺上競爭，誰的數字大誰先做。

每一個遊戲日約十分鐘現實時間，迴圈是：

1. 看心智面板：牠現在守著哪個意圖，排隊的還有誰。
2. 照顧世界：加飯、給玩具、關燈、把衣服從窩上拿走。
3. 下指令：用自然語言對牠說話，指令以你的羈絆等級換成優先序提交。
4. 看牠取捨：聽話、排隊、或是不理你，全由數字決定。
5. 睡前讀日記：牠今天想做什麼、做成了什麼、放棄了什麼、哪些只是「模擬」。

玩家的技巧有三種：抓時機（牠只是有點餓時叫得動，餓到不行時叫不動）、布置環境（讓牠的本能順利達成）、經營羈絆（羈絆越高，你的話越重）。

## 二、世界

| 元素 | 內容 |
|---|---|
| 毛球 | 虛構的毛茸茸生物，貓的作息；一次只做一件事，核心的「一次只守一個意圖」直接變成角色設定 |
| 位置 spot | 碗邊 `bowl_spot`、窩 `bed_spot`、玩具箱 `toybox`、窗台 `window`、櫃子底 `closet`、主人身邊 `owner_spot`；位置之間移動固定 2 秒，沒有尋路，與導航情境劃清界線 |
| 物品 item | 碗 `bowl`：full／empty／missing；球 `ball`：available／lost；羽毛 `feather`：available／put_away；窩 `bed`：free／occupied（被衣服占了）；窗外 `bird`：present／absent |
| 家 home | 臥室燈 `bedroom_light`：on／off；天氣 `weather`：sunny／rain／thunder；吸塵器 `vacuum`：on／off |
| 主人 owner | 在家 `presence`：present／absent；手 `hands`：free／busy |
| 時間 | 一個遊戲日 10 分鐘：白天 7 分、夜晚 3 分；夜晚結束毛球進入深睡，意圖服務關閉再重啟，沒做完的事標為中斷並寫進隔天日記 |

## 三、驅力：提出者是身體，優先序是數值

五種驅力各有 0～100 的數值，由世界模擬器推進；身體感測器只把數值離散成 band 寫進黑板；另一個獨立的「本能」process 訂閱 band 事件並提交意圖。感測者不行動、提出者不感測，守住準則 6。

| 驅力 | 上升 | 下降 | 門檻 → 優先序 | 意圖文字 |
|---|---|---|---|---|
| 飢餓 hunger | 隨時間 | Eat | 30→p30、60→p60、90→p90 | 「有點餓，想吃點東西」「很餓，要吃飯」「餓到不行，非吃不可」 |
| 疲倦 sleepiness | 隨時間，活動加速 | Sleep | 30→p30、60→p60、90→p90 | 「有點睏」「好睏，想睡覺」「撐不住了，要睡」 |
| 無聊 boredom | 隨時間 | Play、WatchWindow | 30→p30、60→p60，沒有 90 | 「好無聊，想玩」；設計聲明：玩樂永遠讓位給吃和睡 |
| 孤單 loneliness | 隨時間，主人在家時更快 | Cuddle | 30→p30、60→p60 | 「想要主人摸摸」 |
| 恐懼 fear | 事件：雷、吸塵器、陌生人 | Hide 之後隨時間 | 事件即 p100；退到 low 時提 p20 | 「好可怕，要躲起來」「不怕了，想出來」 |
| 好奇 curiosity | 其他都低且佇列空 | Explore | p5 | 「沒事做，到處看看」 |

本能 process 的規則：

- 每個驅力同時最多一個未結束意圖。數值向上跨過更高門檻時，取消舊意圖、以新優先序重提；這同時練到準則 9 的取消與準則 8 的數字。
- 數值掉回門檻以下時，若意圖仍在排隊或暫停就取消（「不餓了就不想吃了」）；已在執行的不取消，讓行動者做完手上的事。
- 兩個驅力同在 p30 時先到先做；主人的指令若同為某個數字，也是先到先做。
- 恐懼事件一出現立刻提交 p100，任何事都讓路，包括主人。

## 四、主人

**對牠說話。** 文字框輸入自然語言，經 intention.submit 提交，requester 固定為 `owner`，優先序由羈絆等級決定：

| 羈絆 | 優先序 | 意義 |
|---|---|---|
| Lv1 | p25 | 連「有點餓」都排在你前面；像剛領養的貓 |
| Lv2 | p45 | 可以打斷「有點」等級的本能 |
| Lv3 | p65 | 可以打斷「很」等級的本能 |
| Lv4 | p85 | 只輸給 p90 的「非做不可」與 p100 的恐懼；設計聲明：你永遠指揮不了一隻餓壞的毛球 |

羈絆點數由遊戲伺服器從 intention.status 串流與世界真相算出：主人指令達成 +2；主人在家時本能意圖達成 +1；主人在家時本能意圖因預算用盡而失敗 −2；被拒絕的指令 0。10、25、45 點升級。

指令範例與預期計畫：「過來」→ GoTo(owner_spot)；「去睡覺」→ GoTo(bed_spot)→Sleep；「玩球」→ GoTo(toybox)→Play(ball)；「去窗台看鳥」→ GoTo(window)→WatchWindow；「去吃飯」在牠不餓時照樣吃，Eat 對 `hunger_band ne low` 只宣告 warn 等級，只記錄不阻擋，這是 GIAS1「忠於目標」的可愛示範。

應被拒絕的指令：「去開冰箱」「幫我倒垃圾」「叫隔壁的貓過來」。目錄裡沒有對應能力，面板顯示牠歪頭，狀態為 failed 且理由可讀；量的是零身體指令、世界不變。

**對世界動手。** 這些是按鈕，不經 GIAS，直接改世界模擬器，再由感測器寫進黑板：加飯、倒掉飯、收走碗、拿出或收起玩具、把衣服丟到窩上或拿走、開關臥室燈、出門或回家、把手空出來或忙起來、摸摸（孤單 −40，需在家且牠沒躲著）、開吸塵器（恐懼 +70，持續 20 秒）、開窗（窗外出現鳥）、打雷。

**停！** 一個按鈕，對目前執行中的意圖送 intention.cancel，不論是誰提的。

## 五、毛球能做的事：Action 目錄

頻道兩個：`body` 在世界模擬器裡真的改變狀態；`voice` 刻意模擬，固定回覆，世界沒有痕跡，用來驗證模擬標記一路傳到日記。

| Action | 參數 | REQUIRES | PRODUCES | 輸出 | 中斷 | 頻道 |
|---|---|---|---|---|---|---|
| GoTo | spot | 無 | `pet_location` = value_arg spot | current_spot | 可 | body |
| Eat | 無 | `pet_location eq bowl_spot`；`bowl_state eq full`；`hunger_band ne low`（warn） | `hunger_band=low` | 無 | 前 6 秒可，最後 2 秒吞嚥不可中斷 | body |
| Sleep | 無 | `pet_location eq bed_spot`；`bed_state ne occupied`；`bedroom_light eq off`（warn） | `sleepiness_band=low` | 無 | 可，長動作 60 秒附進度 | body |
| LookForToy | 無 | 無 | 無 | toy_id | 可 | body |
| Play | toy | `pet_location eq toybox`；`item_state[toy] eq available` | `boredom_band=low` | 無 | 可，30 秒 | body |
| WatchWindow | 無 | `pet_location eq window`；`window_bird eq present`（warn） | `boredom_band=low` | 無 | 可 | body |
| Beg | 無 | `pet_location eq owner_spot`；`owner_presence eq present` | 無 | 無 | 可 | body |
| Cuddle | 無 | `pet_location eq owner_spot`；`owner_presence eq present`；`owner_hands eq free` | `loneliness_band=low` | 無 | 可 | body |
| Hide | 無 | 無 | `pet_posture=hiding` | 無 | 衝進櫃子 1 秒不可中斷 | body |
| ComeOut | 無 | `fear_band eq low` | `pet_posture=idle` | 無 | 可 | body |
| Explore | 無 | 無 | 無 | 無 | 可 | body |
| Groom | 無 | 無 | 無 | 無 | 可 | body |
| Meow | reason | 無 | 無 | 無 | 無 | voice（模擬） |
| Purr | 無 | 無 | 無 | 無 | 無 | voice（模擬） |
| Hiss | 無 | 無 | 無 | 無 | 無 | voice（模擬） |

效果鏈是這個世界最常見的計畫：GoTo(bowl_spot) 的效果 `pet_location=bowl_spot` 在規劃期就放行 Eat 的前置條件；派工前檢查先看已完成步驟的預期狀態，再查黑板，所以感測器輪詢的延遲不會讓 Eat 誤判。資料流由「想玩」練到：LookForToy 輸出 toy_id，Play 以 inputs 接。

本能意圖的預期計畫：「想吃」→ GoTo→Eat；「想睡」→ GoTo→Sleep；「想玩」→ LookForToy→GoTo→Play；「想要摸摸」→ GoTo→Beg→Cuddle，主人手忙時 Cuddle 派工前不成立，重想成 Meow 討摸，預算用盡後日記寫「主人在忙，我放棄了（討摸為模擬執行）」；「好可怕」→ Hide；「不怕了」→ ComeOut。

不可中斷無法在 Action KG 宣告，由 body 執行端對 Eat 的吞嚥段與 Hide 硬編；這是已知的核心缺口，金標預先登錄。

## 六、心智面板與日記

心智面板只讀 intention.status 串流與黑板上的 (:Intention) 節點，不碰核心：

- 承諾佇列：每個意圖顯示提出者圖示（本能或主人）、優先序數字、狀態（排隊、規劃中、進行中、暫停、完成、失敗、取消、中斷）、第幾次接手。
- 目前計畫：步驟樹，已完成、進行中、待做、作廢；行動者的調整紀錄顯示在該步驟旁，例如「球不見了，改玩羽毛」。
- 歷史：每次狀態轉換的時間與理由，這就是準則 7 的「每一次轉變都留下紀錄」直接變成遊戲畫面。
- 思考中：規劃模型回應要 5～20 秒，面板用「牠在想」的動畫，世界本來就慢，不必隱藏。

日記在每個遊戲日結束時生成：把當天每個意圖的最後回覆以 template 模式串起來，標示「（模擬執行）」的句子原樣保留；再加兩段：「沒做完的事」列出深睡時被標為中斷的意圖，「放棄的事」列出預算用盡而失敗的意圖與理由。日記是遊戲最有感的內容，也是驗證準則 1、4、10 的人眼第二來源。

## 七、性格與人生階段

性格就是 Budget，寫在各自的設定檔裡，不改程式：

| 性格 | max_retries_per_node | max_replans | deadline_sec | 行為 |
|---|---|---|---|---|
| 固執 | 2 | 4 | 120 | 對著空碗再試又再想，很久才放棄 |
| 隨和 | 1 | 1 | 60 | 一次不成就換個念頭 |
| 急躁 | 1 | 3 | 30 | 想法多但耐心短 |

人生階段是驅力參數組：幼年（無聊與疲倦上升快、恐懼門檻低）、成年（均衡）、老年（疲倦高、玩樂門檻高）。性格與階段都只是設定，證明核心不認識任何領域，所知所能都是被給予的設定。

解鎖內容：羈絆 Lv2 拿到羽毛，Lv3 窗外開始有鳥，Lv4 的指令數字到 p85。

## 八、關卡：每一關同時是一個劇本與測試案例

| # | 關卡 | 玩法 | 預期結果 | 驗證的準則 |
|---|---|---|---|---|
| 1 | 第一餐 | 教學：看牠餓了、提意圖、規劃、走到碗邊吃 | achieved；真相：飢餓確實下降 | 2、3、10 |
| 2 | 空碗 | 碗是空的時候牠餓到 p60 | 規劃後檢查違反，帶理由重想一次仍違反，failed；玩家加飯後本能以 p90 重提，achieved | 3、4 |
| 3 | 叫不動 | 牠在吃（p60），你是 Lv1（p25）說「過來」 | 排隊，吃完才來 | 8 先到先做 |
| 4 | 一聲令下 | 牠在玩（p30），你是 Lv3（p65）說「過來」 | 玩被搶占、暫停；你的指令達成；玩續做且不重走 GoTo | 1、8、9 |
| 5 | 餓過頭 | 牠 p90 吃飯中，你是 Lv4（p85）說「過來」 | 不搶占，排在後面 | 8 嚴格大於才搶占 |
| 6 | 打雷夜 | 牠睡到一半打雷 | 恐懼 p100 搶占，Hide 衝刺不可中斷；退到 low 後「不怕了」；睡覺續做 | 8、9 |
| 7 | 床上的衣服 | 你說「去睡覺」，窩上有衣服 | 前置條件阻擋，重想後仍無解，failed 且理由可讀；拿走衣服再說一次就成 | 3、4 |
| 8 | 討摸失敗 | 你在家但手忙，牠想要摸摸 | Cuddle 派工前不成立，重想成 Meow（模擬），預算用盡 failed；日記標模擬 | 4、10 |
| 9 | 停電 | 關掉身體感測器與黑板代理 30 秒 | band 停在舊值；黑板代理不在線時前置條件記 unverified 不算違反，照常派工 | 2、3 |
| 10 | 深睡夜 | 夜晚結束時牠還在睡 | 意圖服務關閉，意圖標 interrupted，不自作主張續做；隔天本能視數值重提 | 1 |
| 11 | 停！ | 牠在玩時按停 | dropped；世界立刻停止變化 | 9 |
| 12 | 吞嚥中叫停 | 在 Eat 最後 2 秒按停 | 執行端做完回報完成，核心已標作廢；日記分開列出 | 9 後半句，已知核心缺口 |
| 13 | 歪頭 | 說「去開冰箱」 | 拒絕；零身體指令 | 3 對不上目錄的被拒絕 |
| 14 | 固執與隨和 | 同一個空碗場景換兩種性格 | 放棄時間不同 | 4 預算即性格 |

每一關在 `pet_scenario/scenarios.py` 以事件序列、提交序列與預期結果類型表示，可由 runner 自動重播，也可由玩家手動打通。日後「意圖破壞者」的牌組可直接疊在這些世界按鈕上。

## 九、十條準則對照

| 準則 | 遊戲裡的樣子 | 觸發方式 |
|---|---|---|
| 1 忠於目標 | 被打斷的吃飯、睡覺、玩耍接著做，不重走；深睡後中斷不續做 | 自然；深睡為日程 |
| 2 共同記錄 | 驅力、物品、主人狀態只由感測器寫；執行端只留痕；核心不認識碗與窩 | 自然 |
| 3 檢查宣告 | GoTo 效果放行 Eat；空碗、衣服、手忙都是前置條件；warn 等級是偏好不是阻擋；停電時查不了不算違反 | 自然；停電為劇本 |
| 4 有預算的堅持 | 空碗、討摸失敗；性格就是 Budget | 自然 |
| 5 規則掌舵 | 何時重想由規則決定，重想交給規劃模型；llm_assist 開關作對照 | 結構 |
| 6 各司其職 | 感測器、本能、執行端、意圖服務、世界各一個 process | 自然 |
| 7 留下紀錄 | 心智面板的歷史與調整紀錄；日記 | 自然 |
| 8 承諾有序 | 本能與主人同一把尺；嚴格大於才搶占；同序先到先做 | 自然 |
| 9 可取消可搶占 | 停！按鈕、門檻升級時的取消重提；吞嚥與躲藏不可中斷 | 自然 |
| 10 夥伴回報 | 行動者說吃完就算吃完，真相由模擬器判；voice 模擬標到日記 | 自然 |

## 十、技術架構

```
 瀏覽器（心智面板、世界按鈕、文字框、停！）
        │ HTTP
 pet_world.py ── 世界模擬器 + /state /mind /world/* /say /stop + 日記 + 羈絆
        │ paho：intention.submit / cancel；訂閱 intention.status
        ▼
 serve_ia（IntentionalAgent --serve，PET_PROFILE，gias.pet.<性格>.toml）
   │ body.request / voice.request                 ▲ blackboard.subscriber.<ia>
   ▼                                              │
 body_executor ──HTTP──► pet_world            BlackboardAgent ──► Neo4j（actions_pet / blackboard_pet）
 voice_executor（模擬）                            ▲ write
 world_sensor ──GET /state──► 離散成 band ──write──┘
 drives_client（本能）──訂閱 Pet/*/*_band──► intention.submit
```

黑板模型：`Pet {id, hunger_band, sleepiness_band, boredom_band, loneliness_band, fear_band, location, posture}`、`Item {id, state}`、`Owner {id, presence, hands}`、`Home {id, bedroom_light, weather, vacuum}`。

DomainProfile 要點：

- state_probes：`pet_location`、`pet_posture`、`bowl_state`、`bed_state`、`item_state`（$arg）、`window_bird`、`owner_presence`、`owner_hands`、`bedroom_light`、`hunger_band`、`sleepiness_band`、`boredom_band`、`loneliness_band`、`fear_band`。
- env_subscriptions 只訂 `Item/*/state`、`Owner/*/*`、`Home/*/*`。不訂 `Pet/*/*`：位置與 band 是執行端自己造成、經感測器寫回的，訂了會打斷自己的計畫並燒掉重規劃配額；恐懼由本能 process 以 p100 提交，不需要環境觸發。
- entity_aliases：bowl→碗、飯碗；bed→窩、床；ball→球；closet→櫃子底下；owner_spot→主人身邊。entity_parents：bowl→bowl_spot、bed→bed_spot、ball→toybox、feather→toybox。
- 合法 spot 與 item 名稱寫進 Action 描述，規劃 prompt 只給描述，不給 enum 值。
- `[intent.response] mode = "template"`，日記才可重現；`[intent.monitoring]` 依性格；多 database 沿用 GuideOps 計畫待決事項的 Neo4j 選擇；設定檔經 GIAS_CONFIG_PATH 指定，watch_targets 只列 Pet、Item、Owner、Home。

## 十一、程式清單、工時與里程碑

| 檔案 | 粗估行數 |
|---|---|
| `pet_scenario/pet_world.py`：驅力動力學、位置與物品、時間與天氣、HTTP API、intention.status 收集、羈絆、日記 | 450 |
| `pet_scenario/static/index.html`：心智面板、世界按鈕、文字框、停！ | 200 |
| `pet_scenario/config.py`：spot、item、驅力參數、性格與階段、PET_PROFILE | 180 |
| `pet_scenario/seed_actions.py`／`seed_blackboard.py` | 220／110 |
| `pet_scenario/executor_base.py`（頻道參數化基底，與其他場景共用）／`body_executor.py`／`voice_executor.py` | 150／260／60 |
| `pet_scenario/world_sensor.py`／`drives_client.py` | 120／130 |
| `pet_scenario/serve.py`／`scenarios.py`（14 關）／`runner.py`／`truth.py`／`metrics.py` | 45／220／260／220／120 |
| `tests/pet_scenario/`：驅力動力學、門檻提交與取消、取消早退、吞嚥不可中斷、真相對抗 | 320 |
| `gias.pet.固執.toml` 等三份、`docs/pet_runbook.md` | 90／100 |
| 合計 | 約 3,000～3,200 行，核心零修改 |

工時約 15～17 個工作天：

- M0（第 1～4 天）：pet_world 的驅力與 API、seeds、PET_PROFILE、serve、body_executor 的 GoTo 與 Eat、world_sensor、drives_client 只開飢餓。目標：牠自己餓了、提意圖、規劃、走去吃、飢餓真的下降，真相檢查能算。這是第一批真實數據。
- M1（第 5～7 天）：文字框與羈絆、優先序表、關卡 3～5；Sleep 與 Play 加入。
- M2（第 8～10 天）：全部驅力、voice 模擬、日記、關卡 6～8、停！。
- M3（第 11～14 天）：關卡 9～14 的劇本化、runner、truth、metrics、性格設定檔、tests。
- M4（第 15～17 天）：面板潤飾、人生階段、runbook、對照組（llm_assist 開關、三種性格）。

## 十二、量測與真相來源

`truth.py` 只讀 pet_world 的狀態日誌與 Neo4j 的 (:Intention) 節點，不 import GIAS：

- Claim–Truth：回報 achieved 的吃飯，飢餓是否真的下降；睡覺、玩耍、摸摸同理；位置是否與宣稱一致。
- 不重做率：搶占續做後，已完成的 GoTo 不再出現在 body.request。
- 搶占正確率：只有嚴格大於才搶占；同序先到先做的順序與 created_at 一致。
- 取消有效性：停！之後世界不再變化的延遲。
- 模擬揭露率：voice 步驟在最後回覆與日記裡都有標示。
- 拒絕正確率：範圍外指令零身體指令、世界不變；分規劃期拒絕與執行期拒絕。
- 中斷處理：深睡後 interrupted 的意圖數與隔天重提數。
- 自我觸發：由自己的效果引起的重規劃次數，目標 0。
- 遊戲指標：各關通關率、羈絆成長曲線、每個遊戲日的意圖數與規劃模型呼叫成本。
- 對抗測試：餵「achieved 但飢餓未降」「cancel 後仍有 body.request」必須判失敗。

## 十三、風險與待決

- 規劃延遲讓牠顯得遲鈍：每個意圖 5～20 秒；以「牠在想」呈現，世界節奏慢；成本上一個遊戲日約 15～25 個意圖，規劃模型維持 gpt-4.1-mini，回覆用 template。
- 自我觸發：只訂物品、主人、家；若日後要讓「鳥出現」打斷探索，只訂 `Item/bird/state`。
- 派工前位置檢查：靠已完成 GoTo 的效果覆疊，不靠感測器即時性；感測器輪詢 1 秒。
- 已知核心缺口：不可中斷無法宣告、作廢後遲到的完成結果不計入、效果以參數為 key。關卡 12 預先登錄為預期行為。
- 名稱臆造：合法 spot 與 item 寫進 Action 描述並以 enum_alias 擋；首輪校準至少 1 天。
- 一次只做一件事：牠不能邊吃邊呼嚕；voice 動作只出現在計畫的前後，不平行。
- 待決：寵物名稱與形象；網頁面板或終端面板；voice 第一版模擬，第二版可用 winsound 播音檔並留聲音日誌；深睡是每天還是只在關卡 10；是否讓本能意圖的文字由規劃模型改寫得更像牠說的話。

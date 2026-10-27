# 以 Petoi Bittle X V2 實現 GIAS1 陪伴寵物：提案

> 2026-10-28。延續 docs/cat_robot_plan.md（實體陪伴寵物）與 docs/pet_game_plan.md（數位分身），改以現成可開發的四足機器狗 Bittle X V2 當身體。心智仍是 GIAS1；核心零修改。
> 規格來自 Petoi 官方指南、文件中心與經銷商頁面（文末列出）；兩處文件互相矛盾的地方已標出，收到機器第一天要實測。

## 一、結論

**可以做，而且是 GIAS 第一次指揮真實世界的執行端最便宜的路。** Bittle X V2 有一顆 ESP32 主板、九顆伺服、內建陀螺儀、背部觸摸、語音模組與藍牙、Wi-Fi，一條文件化的序列指令協定與 Python API；對 GIAS 來說，牠就是一個「pose／move 頻道的執行端」，用現有的 `<channel>.request／.result／.cancel／.progress` 慣例接上即可，第一週就能讓 GIAS 下達「坐下、起立、走到窩邊」並拿到真實回報。

三件事要誠實面對，都有對策：

1. **牠不會自己充電。** 原廠電池用電池本體的 Type-C 充電，充電時切斷對機器的供電，且說明要先關機。「沒電自己找插孔」第一階段只能做到「自己走到窩、趴下、叫人插電」；全自動充電是第二階段的硬體實驗（見第六節）。
2. **續航約一小時，充電約兩小時。** 牠一天有三分之二在睡，這很像貓；永遠活著的意思變成「醒著的時候很短，但從不斷線」。
3. **牠沒有定位。** 沒有里程計與光達，走路是開迴路。家裡的空間縮成一塊三乘三公尺的「領地」，用天花板或桌角的一台攝影機加 AprilTag 定位牠與窩，眼睛暫時長在天花板上；這對 GIAS 完全合理，感測者本來就是獨立的夥伴。

## 二、Bittle X V2 的事實

| 項目 | 內容 | 來源與備註 |
|---|---|---|
| 主板 | BiBoard V1，ESP32；指南寫 V1 為 4M flash、V0 為 16M，經銷商寫 ESP32-WROOM-32D、520K SRAM、16MB flash，以實物為準 | 指南、經銷商 |
| 關節 | 八個腿關節加一個頸關節，伺服最高 8.4 V | 經銷商 |
| 感測 | 內建陀螺儀（開機掃描到 MPU6050 與 ICM42670）、背部觸摸、蜂鳴器、內建語音模組 | 指南 |
| 語音模組 | 35 條固定指令，中英文可切換，無喚醒詞；可自錄 10 條六音節以內的指令；`XAc` 開啟語音反應、`XAe` 進入學習 | 指南 |
| 連線 | USB Type-C 序列、藍牙序列（裝置名 Bittle***_SSP）、Wi-Fi（韌體 1.2.6 beta 以上，經 Web Coding Blocks 設定 2.4 GHz；網路重設後自建熱點 192.168.4.1） | 指南、文件中心 |
| 擴充 | 擴充帽上有 Grove 座（I2C、類比、數位）與 UART2；Raspberry Pi 經 UART2 接線（5V→5V、GND→GND、GPIO14→RX2、GPIO15→TX2，開關撥到 UART2），V0 與 V1 的軟體設定相同；套件附 Pi 支架；指南建議小型 Pi（3A+、Zero），經銷商寫支援 3、4、5 | 文件中心；需實測 |
| 視覺 | 選購 MU 相機（色塊、球、人體、卡片，I2C）或 Petoi AI Vision 模組（5MP、120°、WiseEye2，人體、人臉、姿態、手勢、80 類物件）；FAQ 提到相機接擴充帽後可能無法工作，需實測 | 文件中心、經銷商 |
| 電池 | 7.4 V 1000 mAh 鋰電池，USB 5 V 1 A 充電約 2 小時，續航約 1 小時；充電走電池本體 Type-C，充電時切斷供電；電量低時暫停動作並發出降音 | 經銷商、指南 |
| 體型 | 200×110×110 mm，約 265～290 g | 經銷商 |
| 序列協定 | `k` 技能（`ksit`、`kup`、`kwkF`、`ktrL`、`kbk`…，名稱以 InstinctBittle.h 為準）、`m`／`i` 單關節與多關節角度、`j` 讀關節角、`d` 休息、`p` 暫停、`G` 陀螺儀平衡開關、`v` 印一次陀螺儀、`b` 音符、`z` RandomMind 開關、`?` 查詢、`R`／`W` 讀寫腳位（`a` 類比、`d` 數位，需 GROVE_SERIAL_PASS_THROUGH）、`c`／`s` 校正、`X` 模組模式（`XB` 背部觸摸、`XU` 超音波、`XC` 相機、`XS` 開 UART2）、`fl`／`fr` 拉腿教學與重播 | OpenCat.h、指南 |
| Python API | PetoiRobot：`autoConnect`、`sendSkillStr`、`sendCmdStr`、`rotateJoints`、`getAngleList`、`readAnalogValue`、`readDigitalValue`、`play`、`deacGyro` | 文件中心 |
| 沒有的東西 | 文件中沒有 BiBoard 的電池電壓查詢指令（NyBoard 才有 VOLTAGE_DETECTION_PIN）；沒有自動回充；沒有里程計 | OpenCat.h、指南 |
| 價格 | 預組裝 Lite 伺服版約 US$339 | 經銷商 |

## 三、架構：心在電腦，身體是 Bittle

```
 kiosk／語音（STT）──► intention.submit ──► serve_ia（IntentionalAgent，PET_PROFILE，gias.bittle.toml）
                                              │ pose.request / move.request / voice.request / sense.request
                                              ▼
   bittle_bridge（PC 上的執行端 process，藍牙或 USB 序列連 BiBoard）
      pose：ksit/kup/d/khi/kstr/kpu… 與頭部 m0        move：閉迴路走路（kwkF/kwkL/kwkR/kbk）直到攝影機姿態到位
      voice：PC 喇叭 TTS 與蜂鳴器旋律 b               sense：拍照、看向目標（m0）
                                              ▲ blackboard.subscriber.<ia>
   eye_sensor（天花板攝影機 + AprilTag：Pet 位置與朝向、人在哪、窩在哪）──write──► BlackboardAgent ──► Neo4j
   body_sensor（序列回讀：陀螺儀 v、關節 j、低電量降音、背部觸摸、語音模組聽到的字）──write──┘
   dock_agent（窩上的 USB 電流計：是否在充電、充飽）──write──┘   drives_client（本能）──► intention.submit
```

- 心智在現有的 Windows 主機：Neo4j、Mosquitto、BlackboardAgent、IA、對話代理、規劃模型與語音辨識合成。
- 第一版身體上不加電腦：PC 經藍牙序列直接指揮 BiBoard，省重量與電；第二版才在背上加 Pi Zero 2 W 或 3A+ 帶相機與麥克風。
- 板上的「自己的意志」要關掉：`z` 關 RandomMind，語音模組的直接反應關閉（`XAc` 的反向，實測指令），改由序列讀出「聽到什麼」寫進黑板，由 GIAS 決定做不做；背部觸摸留作反射（摸了會抬頭）並同時寫黑板。這是準則 6：行動者只做手上的事，意志只有一個。

## 四、GIAS1 在牠身上

**提出者與優先序**（沿用 docs/pet_game_plan.md）：本能 p5～100、主人依羈絆 p25／45／65／85、照顧者排程 p70、安全 p100。

**電量驅力**：文件沒有 BiBoard 的電壓查詢，第一版用「醒著的分鐘數」估算，並以序列讀到的低電量降音當 p100 的硬觸發；第二版在 Grove 類比腳位接分壓電阻用 `Ra` 讀電壓，或在窩上量充電電流。

| 醒著時間 | 意圖 | 優先序 |
|---|---|---|
| 35 分鐘 | 「有點累，想回窩」 | p30 |
| 45 分鐘 | 「很累，要回窩」 | p60 |
| 52 分鐘 | 「撐不住了，非回窩不可」 | p90 |
| 板子發出低電量降音 | 「停下來，叫人」：`d` 趴下、app 推播 | p100 |

**Action 目錄**：

| 頻道 | Action | 對應指令 | 前置條件 | 效果 | 中斷 |
|---|---|---|---|---|---|
| pose | Sit、Stand、Rest、Stretch、Hi、Wave、PushUp、PlayDead、Nod、LookAt(target) | `ksit`、`kup`、`d`、`kstr`、`khi`、`kwh`、`kpu`、`kpd`、`m0 角度` | `pet_posture ne charging` | `pet_posture=value` | 技能播完一段才停 |
| move | GoTo(spot)、Follow(person)、Turn(dir)、Dock | `kwkF`／`kwkL`／`kwkR`／`kbk`／`ktrF` 閉迴路，Dock 最後是走上窩、`d` 趴下 | `pet_location` 已知、`spot_free eq yes`、`battery_band ne critical`、`tracking eq ok`（查不到不算違反） | `pet_location=value_arg spot`、`docked=yes` | 可；上窩最後一步不可中斷 |
| voice | Say(text)、Melody(mood)、Bark | PC 喇叭 TTS、`b` 旋律 | 無 | 無 | 說完一句才停 |
| sense | TakePhoto、Describe(target)、FindPerson | 天花板攝影機與（第二版）背上相機 | `camera_online eq yes` | 輸出 photo_id、description | 可 |
| charge | Sleep(until=80%) | 窩上的電流計判定開始與結束；身體斷電期間由 PC 端的 dock_agent 回報進度 | `docked eq yes` | `battery_band=high` | 可取消，但身體斷電時取消等於「叫人拔線」 |

**十條準則**：

| 準則 | 在 Bittle 上的樣子 |
|---|---|
| 1 | 巡視到一半回窩，充完接著巡；PC 重啟後意圖標 interrupted 寫日記 |
| 2 | 位置、朝向、人、窩由攝影機感測者寫；觸摸、陀螺儀、聽到的字由 body_sensor 寫；意志只拿問出來的答案 |
| 3 | 窩被人占住 `spot_free` 不成立就不派工重想；攝影機掛了 `tracking` 查不到不算違反，照走，走丟再說 |
| 4 | 走不到窩：再試一次、換方向、放棄並趴下叫人；預算是性格 |
| 5 | 規則決定何時重想，規劃模型重想，答案仍要過關 |
| 6 | 板上的 RandomMind 與語音直接反應關閉；感測者只寫所見 |
| 7 | 每一次轉變都在 app 與日記 |
| 8 | 餓壞的狗叫不動：p90 回窩途中 Lv4 的你也叫不動 |
| 9 | 「停」送 `p` 立刻停；上窩最後一步與說到一半的句子做完再停 |
| 10 | 身體說坐下就算坐下，真相由攝影機的姿態判；第一版沒裝的 Bark 以模擬回覆並標到最後回覆 |

## 五、感知與對話

- **眼睛在天花板**：一台 USB 攝影機俯視三乘三公尺的領地，AprilTag 貼在牠背上與每個窩、每個位置上；輸出 Pet 的位置與朝向、人在哪一格、窩是否被占。這是第一版唯一的定位來源，也是 Claim–Truth 的真相來源。
- **身體感測**：序列回讀陀螺儀（`v`）判翻倒、關節角（`j`）判姿態、低電量降音、背部觸摸事件、語音模組辨識出的字。
- **對話**：PC 麥克風與喇叭；對話代理依 docs/cat_robot_plan.md 第八節，根據感知、黑板、今天的意圖歷史、對人的記憶、自己的狀態回答，並訂閱 intention.status 交代自己：「我要回窩了，你幫我插一下電」。指令從對話抽出提交；板上的 35 條固定語音指令改為「聽到了」事件，不直接執行。
- **第二版**：背上加 Pi Zero 2 W 或 3A+、Pi 相機與小麥克風，眼睛才長在身上；或改用 Petoi AI Vision 模組讓板子自己看人臉與手勢，由序列回讀。

## 六、充電：從「叫人插」到自動回充

| 階段 | 做法 | 心智看到的 |
|---|---|---|
| A 半自動（第一版） | 走到窩、`d` 趴下、旋律加 app 推播；人把 Type-C 插上，供電切斷，身體離線；窩上的 USB 電流計看到電流即「開始睡」，電流掉到涓流即「睡飽」；人按電源鍵喚醒 | Sleep 意圖由 dock_agent 回報進度與完成；身體離線不是失敗，是睡著 |
| B 自動（硬體實驗） | 腹部彈簧接點對窩上的接點，接到電池的 5V 充電輸入；窩上一顆 ESP32 控制通電與量電流 | 同上，少了「叫人」 |
| B 的兩個未知 | (1) 充電結束後 BiBoard 是否自動復電，或要按鍵；若要按鍵，窩上加一顆小伺服壓鍵，或改用有 power-path 的 2S 電池與 BMS 讓牠邊充邊醒 (2) 改裝電池的安全 | 第一天實測，決定 B 做不做 |

無論 A 或 B，「只有充電時在睡覺」是字面的：充電時板子沒電，牠的心仍在電腦上醒著，app 裡看得到牠在睡、幾點會醒。

## 七、創意擴充（Bittle 專屬）

- **教牠新把戲是韌體內建的**：拉牠的腿示範（`fl`）、重播（`fr`）、存成技能；對話代理把它登記成 Action KG 的新能力，下次說「打招呼」就會。能力是資料，這次連資料都是人手教的。
- **把戲當作羈絆獎勵**：Lv1 只會坐下起立，Lv2 伸懶腰與揮手，Lv3 伏地挺身與裝死，Lv4 後空翻。
- **旋律是牠的語氣**：沒有喇叭時用蜂鳴器 `b` 旋律表達心情，PC 喇叭的 TTS 是「翻譯機」。
- **尿尿與裝死是幽默**：`kpee` 在你罵牠時出現，`kpd` 在你說「壞狗」時出現，由本能的「委屈」驅力提出。
- **窩不只一個**：三個 AprilTag 窩，牠挑最近的空窩；兩隻 Bittle 搶窩由優先序裁決。
- **Grove 感測器當情緒入口**：光線感測找「曬太陽」的位置，手勢感測聽手勢的「來」與「停」，PIR 知道有人進來。
- **帶手臂的版本會叼東西**：Bittle X V2 + Arm 可以把小東西拿給你，真貓的「禮物」第一次字面成立。
- **數位分身永遠在**：牠睡覺時，遊戲版的牠在 app 裡也在睡；同一個心、兩個身體。

## 八、階段與工時

| 階段 | 內容 | 產出 | 工時 |
|---|---|---|---|
| 0 | 到貨實測：藍牙序列、`ksit`／`kup`／`d`／`kwkF`、語音模組關閉方式、充電時是否斷電、充完是否自動復電、Pi 支架與 UART2 | 事實清單，決定充電 B 做不做 | 2～3 天 |
| 1 | bittle_bridge：pose 與 voice 頻道接 GIAS；gias.bittle.toml、seeds、PET_PROFILE；kiosk 下「坐下」「打招呼」 | **GIAS 第一次指揮真實執行端**，Claim–Truth 用攝影機判姿態 | 1～2 週 |
| 2 | 天花板攝影機與 AprilTag 定位感測者；move 頻道閉迴路走路；三個位置與一個窩；GoTo／Follow／Dock | 牠在領地裡走到你指的地方 | 2～3 週 |
| 3 | 電量驅力、半自動充電、dock_agent、睡眠語意、深睡與中斷 | 一整天不需人工搭救（除了插線） | 1～2 週 |
| 4 | 本能全開、對話代理、語音指令、日記、app | 會聊、會聽、有脾氣 | 2～3 週 |
| 5 | 自動回充硬體實驗、Pi 與相機上身、把戲解鎖、試住 | 完整的牠 | 2～4 週 |

合計約兩個半到三個月兼職；硬體約 NT$ 15,000（機器 US$339、攝影機、AprilTag、USB 電流計、窩）。

## 九、量測

- 第一次真實執行端：pose 的 Claim–Truth（牠說坐下，攝影機看到的姿態是不是坐）、`.result` 延遲、取消到停止的延遲。
- 移動：到達誤差（公分）、GoTo 成功率、翻倒次數、走丟後 `tracking` 查不到仍派工的次數與結果。
- 電量：醒著時間估算與實際斷電的誤差、回窩成功率、低電量降音出現時牠離窩多遠。
- 睡眠：Sleep 意圖的開始與結束是否與電流計一致；身體離線期間 Claim–Truth 是否正確標示。
- 意圖層：不重做率、搶占正確率、同序先到先做、拒絕正確率、模擬揭露率、自我觸發零。
- 陪伴：每週問卷；日記被讀的比例。

## 十、風險

- 續航一小時：每個劇本要在 40 分鐘內跑完，長跑靠多隻或多電池。
- 充電斷電：睡眠期間身體完全離線，所有「睡著時聽得到名字」的設計都不成立；第二版若換電池才可能。
- 走路開迴路且地面影響大：地毯與木地板的步態參數不同，閉迴路修正靠攝影機每秒數次。
- 板上語音與 RandomMind 若關不乾淨，會出現不經意志的動作，污染準則 6 的量測；到貨第一天確認。
- 文件矛盾：flash 容量、Pi 型號、相機與擴充帽相容性；以實物為準，Arduino ESP32 套件需固定 2.0.12 版。
- 小而輕：不能背 Pi 4／5，伺服也撐不了太重的頭；第二版只考慮 Zero 2 W 或 3A+。
- 已知核心缺口照舊：不可中斷無法宣告、作廢後遲到的完成結果不計入；上窩最後一步由 bridge 硬編。

## 十一、待決

1. 先買預組裝版（US$339）還是自組版；要不要一起買 AI Vision 模組與 Grove 感測包。
2. 第一版連線用藍牙序列還是 USB 線（USB 線會限制走動範圍，藍牙延遲要實測）。
3. 充電 B 要不要做；若要，改電池或加壓鍵伺服。
4. 領地放哪裡、攝影機裝哪裡。
5. 牠是狗還是貓心狗身；名字。

## 來源

- Petoi 指南 Bittle X V2：https://guide.petoi.com/product/bittle-x-v2
- Petoi 指南全文：https://guide.petoi.com/llms-full.txt
- 序列協定：https://docs.petoi.com/apis/serial-protocol
- Python API：https://docs.petoi.com/apis/python-api
- Raspberry Pi 序列介面：https://docs.petoi.com/apis/raspberry-pi-serial-port-as-an-interface 與 https://docs.petoi.com/apis/raspberry-pi-serial-port-as-an-interfac/for-biboard-v0
- 擴充模組：https://docs.petoi.com/extensible-modules/introduction 、MU 相機 https://docs.petoi.com/extensible-modules/mu-camera
- Web Coding Blocks（Wi-Fi）：https://docs.petoi.com/web-block-base-programming/petoi-web-coding-blocks
- OpenCat.h 指令定義：https://raw.githubusercontent.com/PetoiCamp/OpenCat/main/src/OpenCat.h
- 經銷商規格與價格：https://www.robotshop.com/products/petoi-bittle-x-v2-lite-servo-pre-assembled 、https://www.robotshop.com/products/petoi-bittle-x-v2-lite-servo-construction
- Petoi AI Vision 模組：https://www.makershed.com/products/petoi-ai-vision-module-for-arduino-petoi-bittle-robot-dog

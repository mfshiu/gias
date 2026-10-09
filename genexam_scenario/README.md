# GenExam Scenario：試題生成（評估二）測試套件

驗證 GIAS 在「**限制驅動的試題生成**」任務下，是否能透過 GVR
（Generator → Verifier → Refiner）閉環，於動態約束注入時仍維持原 Intention，
並確實做到 Knowledge Grounding（不幻覺、引用 KG 內容）。

## 測試規劃（共 60 案，限制於生成前固定，不做動態注入）

| 組別 | 案例數 | 範例 | 限制條件 |
|---|---|---|---|
| **single_constraint** | 30 | 「請生成 5 題關於空氣污染的中等難度選擇題」 | 單一限制：難度 **或** 題型 |
| **multi_constraint** | 30 | 「請生成 5 題廢棄物管理的困難選擇題（Bloom Level 4：分析）」 | 三限制全套：難度 + Bloom + 題型 |

Single-Constraint 30 案分為兩組聚焦：
- **15 案聚焦 difficulty**：5 主題 × 3 難度（題型固定 mcq）
- **15 案聚焦 question_type**：5 主題 × 3 題型（不指定難度 / Bloom）

Multi-Constraint 30 案：6 主題 × 5 組固定限制組合，涵蓋 Bloom 1–5 與 4 種題型。

## 模組結構

```
genexam_scenario/
  __init__.py
  config.py          # 6 主題 / 20 子主題 / 55 概念 / 56 事實 / 10 來源
                     # + 約束維度（Difficulty / BloomLevel / QuestionType）
                     # + Agent / Skill / State 列表
  seed_actions.py    # 寫入 Action KG（[kg.neo4j_actions] = 'actions' database）
  seed_blackboard.py # 寫入 Blackboard KG（[kg.neo4j_blackboard] = 'blackboard' database）
  test_cases.py      # 60 個固定限制的測試案例（30 single + 30 multi）
  metrics.py         # 5 項指標：KC / CSR / SC / Task Success Rate / Gen Time
  runner.py          # 單案例執行器（GVR 閉環，真實 LLM 呼叫）
  batch_runner.py    # 批次執行 60 案例 → genexam_scenario_results/run_<ts>/
  analyze.py         # 彙整 CSV + Markdown 報告（投影片風格表格）
  README.md
```

## 單案例執行器 `runner.py`

完整實現 GVR（Generator → Verifier → Refiner）閉環：

| 階段 | 內容 |
|---|---|
| **Plan** | 寫入 `GenerationSession` / `Intention` / `Constraint` 節點 |
| **Retrieve** | 從 Blackboard 抓 topic 對應的所有 `Concept` + `Fact`（KG-RAG 素材） |
| **Generate** | 呼叫 LLM 產出單一題目（含選項 / 答案 / knowledge_refs） |
| **Verify** | 本地 schema 一致性 + 知識依據存在性 + 限制條件對比；可選 LLM-judge 獨立評估 difficulty/bloom/qtype |
| **Refine** | 對未通過的題目呼叫 LLM 改寫（預設最多 2 次） |
| **Finalize** | 寫入 `Question` + `Choice` + `GROUNDED_ON` + 分類學連線，將 session 標為 Finalized |
| **Evaluate** | 呼叫 `metrics.evaluate_case()` 算 5 項指標並回傳 `CaseResult` |

### 指令

```powershell
$py = "C:\Users\mfshi\miniconda3\envs\gias\python.exe"

# Dry-run（不呼叫 LLM，用 mock 題目驗證 pipeline，秒級完成）
& $py -X utf8 -m genexam_scenario.runner single_diff_02 --dry-run --seed 42

# Live LLM（預設）
& $py -X utf8 -m genexam_scenario.runner single_diff_02

# Live + LLM judge（Verifier 用獨立 LLM 重新判定 difficulty/bloom/qtype，更嚴格但較慢）
& $py -X utf8 -m genexam_scenario.runner multi_07 --llm-judge

# 改變 refine 次數上限（預設 2）
& $py -X utf8 -m genexam_scenario.runner multi_07 --max-refine 3

# 輸出 JSON
& $py -X utf8 -m genexam_scenario.runner single_diff_02 --out result.json
```

### 範例輸出

```
=== run_case: single_diff_02 ===
  request : 請生成 5 題關於空氣污染的中等難度選擇題
  topic=air_pollution, count=5, difficulty=medium, bloom=None, type=mcq
  mode    : live (LLM)  llm_judge=False  max_refine=2
  [plan] session_id=qgen_single_diff_02_xxxx
  [retrieve] 12 concepts / 13 facts
  [q1/5] attempt=1 [OK] sc_issues=0 csr_issues=0 ground_issues=0
  ...
  [eval] KC=0.42 CSR=1.00 SC=1.00 success=True elapsed=21.24s refines=0
```

## 批次執行 `batch_runner.py`

逐案執行篩選後的 cases，每案結果 JSON 寫到
`genexam_scenario_results/run_<ts>/<case_id>.json`，並寫一份 `_manifest.json`。
跑完自動呼叫 `analyze.py` 產出 CSV + Markdown 報告（除非 `--no-analyze`）。

### 指令

```powershell
$py = "C:\Users\mfshi\miniconda3\envs\gias\python.exe"

# 完整 60 案（live LLM，約 15~20 分鐘）
& $py -X utf8 -m genexam_scenario.batch_runner

# 快速 smoke test（dry-run，~5 秒）
& $py -X utf8 -m genexam_scenario.batch_runner --dry-run --limit 4 --seed 42

# 只跑 single-constraint 30 案
& $py -X utf8 -m genexam_scenario.batch_runner --category single_constraint

# 只跑 multi-constraint 30 案 + 嚴格 LLM judge
& $py -X utf8 -m genexam_scenario.batch_runner --category multi_constraint --llm-judge

# 只跑某主題（用於除錯）
& $py -X utf8 -m genexam_scenario.batch_runner --topic air_pollution

# 指定特定 case_id（可重複）
& $py -X utf8 -m genexam_scenario.batch_runner --case-id single_diff_02 --case-id multi_07
```

### 篩選參數

| 參數 | 說明 |
|---|---|
| `--category` | `single_constraint` / `multi_constraint` |
| `--topic` | 6 主題之一 |
| `--constraint-focus` | `difficulty` / `question_type` / `all` |
| `--difficulty` | `easy` / `medium` / `hard` |
| `--question-type` | `mcq` / `true_false` / `short_answer` / `cloze` |
| `--limit N` | 只跑前 N 案 |
| `--case-id ID` | 只跑指定 case_id（可重複） |

### 執行參數

| 參數 | 預設 | 說明 |
|---|---|---|
| `--dry-run` | off | 不呼叫 LLM，用 mock 題 |
| `--llm-judge` | off | Verifier 啟用獨立 LLM 判定 |
| `--max-refine N` | 2 | 每題最多 refine 次數 |
| `--pause SEC` | 0.3 | 案例間暫停秒數 |
| `--output DIR` | 自動 | 自訂輸出目錄 |
| `--fail-fast` | off | 第一個錯誤即停 |
| `--verbose` | off | 印出每案逐步 log |
| `--no-analyze` | off | 跑完不自動 analyze |
| `--seed N` | None | random seed |

## 分析報告 `analyze.py`

### 指令

```powershell
# 由 batch_runner 自動呼叫，也可手動重跑：
& $py -X utf8 -m genexam_scenario.analyze genexam_scenario_results\run_20260525_012011
```

### 產出檔案

| 檔案 | 內容 |
|---|---|
| `summary.csv` | 每案一列：case_id / category / topic / constraint_focus / difficulty / bloom_level / question_type / KC / CSR / SC / task_success / gen_time_sec / constraints_required |
| `aggregate.csv` | 依 category 彙整：single / multi / overall 三列 × 5 指標 |
| `by_topic.csv` | 依主題拆分 |
| `by_focus.csv` | 依 constraint_focus 拆分 |
| `report.md` | 投影片風格 Markdown 報告（含 5 指標主表 + 主題拆分 + focus 拆分 + refine 統計 + 失敗案例） |

### 報告主表（對應投影片 SIMULATED RESULTS）

`report.md` 的第一張表格是與你提供的投影片完全對應的格式：

```
| Metric                              | Single-Constraint | Multi-Constraint | Overall |
|-------------------------------------|:-----------------:|:----------------:|:-------:|
| 📖 KC (Knowledge Coverage)           |      55.6%        |      45.5%       |  50.5%  |
| ✅ CSR (Constraint Satisfaction Rate)|     100.0%        |     100.0%       | 100.0%  |
| 🔗 SC (Semantic Consistency)         |     100.0%        |     100.0%       | 100.0%  |
| 🚩 Task Success Rate                 |     100.0%        |     100.0%       | 100.0%  |
| ⏱️  Gen Time (sec/case)              |     18.40s        |      13.45s      |  15.93s |
```

## 評估指標（5 項，對應投影片 EVALUATION OBJECTIVES）

| 指標 | 全稱 | 定義 |
|---|---|---|
| **KC** | Knowledge Coverage | 該 topic 被引用過的 distinct Concept 數 ÷ 該 topic 在 KG 中的全部 Concept 數 |
| **CSR** | Constraint Satisfaction Rate | 同時滿足所有指定限制（difficulty / bloom / question_type）的題目比例 |
| **SC** | Semantic Consistency | 題目通過 schema 一致性檢核（題幹 / 選項 / 答案 / 題型對應）的比例 |
| **Task Success Rate** | — | 案例級成功率：題數達標 AND CSR ≥ 1.0 AND SC ≥ 1.0 |
| **Gen Time** | — | 單案例從請求到 Finalize 的 wall-clock 秒數 |

跨案彙整輸出 `single_constraint` / `multi_constraint` / `overall` 三組數值，
可直接對應投影片的 SIMULATED RESULTS 表格。

### 指令

```powershell
$py = "C:\Users\mfshi\miniconda3\envs\gias\python.exe"

# 看 60 案摘要
& $py -X utf8 -m genexam_scenario.test_cases

# 列出每一案的 case_id / 請求 / 限制
& $py -X utf8 -m genexam_scenario.test_cases --list

# 輸出 genexam_scenario_cases.json
& $py -X utf8 -m genexam_scenario.test_cases --dump

# metrics 模組自我測試（不需 Neo4j / LLM）
& $py -X utf8 -m genexam_scenario.metrics
```

## 前置條件

1. Neo4j 啟動，且 `actions` 與 `blackboard` 兩個 database 已建立（Enterprise edition 可自動建立）；密碼與 `gias.toml` 一致。
2. `gias.toml` 內 `[llm.openai].api_key` 可用（`seed_actions` 每個 action 需呼叫一次 embedding API，共 11 次）。

## 執行：建立 Action KG 與 Blackboard KG

> Windows PowerShell 範例。為避免主控台無法輸出 emoji，全程加 `-X utf8` 旗標。

```powershell
$py = "C:\Users\mfshi\miniconda3\envs\gias\python.exe"

# Step 1：種子 Action KG（一次即可，除非 actions database 被清空）
& $py -X utf8 -m genexam_scenario.seed_actions

# Step 2：種子 Blackboard KG（每次執行新一輪測試前重置）
& $py -X utf8 -m genexam_scenario.seed_blackboard

# 強制重建 blackboard database：
& $py -X utf8 -m genexam_scenario.seed_blackboard --recreate
```

## Action KG 內容（11 個 GVR actions）

依 GVR 閉環的角色分類：

| 角色 | Task | 用途 |
|---|---|---|
| **Coordinator** | `PlanGeneration` | 解析請求，建立 GenerationSession 與 Intention |
| **Coordinator** | `UpdateIntention` | 接收動態事件，更新 / 收緊 / 替換 / 鬆綁約束 |
| **Coordinator** | `FinalizeQuestionSet` | 彙整通過驗證的題目，輸出最終題組 |
| **Generator** | `RetrieveKnowledge` | 從領域知識子圖檢索 Concept / Fact |
| **Generator** | `GenerateQuestion` | 依約束 + 知識依據產出單一題目 |
| **Verifier** | `AssessDifficulty` | 評估題目實際難度 |
| **Verifier** | `AssessBloomLevel` | 評估題目 Bloom 認知層次 |
| **Verifier** | `ClassifyQuestionType` | 確認題目格式符合預期題型 |
| **Verifier** | `VerifyKnowledgeGrounding` | 檢查題目是否能在 KG 找到支持，避免幻覺 |
| **Verifier** | `VerifyConstraintSet` | 整批層級檢查所有約束 |
| **Refiner** | `RefineQuestion` | 依 Verifier 回報問題改寫題目（保留 Intention） |

每個 Action 都帶 `description_embedding`（OpenAI `text-embedding-3-small`），
並建立向量索引 `action_desc_vec`，供 IntentionalAgent 做語意路由。

## Blackboard KG 內容（baseline）

### A. 領域知識子圖（KG-RAG 來源）

```
(:Topic)-[:HAS_SUBTOPIC]->(:Subtopic)-[:HAS_CONCEPT]->(:Concept)
(:Concept)-[:SUPPORTED_BY]->(:Fact)-[:CITED_FROM]->(:Source)
```

| 類型 | 數量 | 範例 |
|---|---|---|
| `Topic` | 6 | `air_pollution`, `waste_management`, `climate_change`, `water_resources`, `biodiversity`, `energy_conservation` |
| `Subtopic` | 20 | `particulate_matter`, `circular_economy`, `greenhouse_gases`, ... |
| `Concept` | 55 | `PM2.5`, `circular_economy`, `co2`, `eutrophication`, ... |
| `Fact` | 56 | 「WHO 2021 指引將年均 PM2.5 安全值由 10 µg/m³ 下修至 5 µg/m³。」 |
| `Source` | 10 | IPCC AR6 / WHO AQG / US EPA / 台灣環境部 / UN SDG / IUCN / IEA / Ellen MacArthur / World Bank / WWF |

### B. 約束分類學（動態約束的目標域）

| 類型 | 節點 |
|---|---|
| `Difficulty` | `easy`, `medium`, `hard` |
| `BloomLevel` | 1 Remember / 2 Understand / 3 Apply / 4 Analyze / 5 Evaluate / 6 Create |
| `QuestionType` | `mcq`, `true_false`, `short_answer`, `cloze` |

### C. GVR Agent 子圖

| Agent | type | skills | 初始狀態 |
|---|---|---|---|
| `CoordinatorBot_01` | Coordinator | Generation_Coordination, Knowledge_Retrieval | Idle |
| `GeneratorBot_01` | Generator | Question_Authoring, Knowledge_Retrieval | Idle |
| `VerifierBot_01` | Verifier | Question_Verification, Knowledge_Retrieval | Idle |
| `RefinerBot_01` | Refiner | Question_Refinement, Knowledge_Retrieval | Idle |

`State` 共 7 個：`Idle`, `Planning`, `Retrieving`, `Generating`, `Verifying`, `Refining`, `Finalized`。

### D. 由 runner 在執行期動態生成的節點（baseline **不**包含）

```
(:GenerationSession)-[:HAS_INTENTION]->(:Intention)
(:GenerationSession)-[:HAS_CONSTRAINT]->(:Constraint)
(:GenerationSession)-[:HAS_QUESTION]->(:Question)
(:Question)-[:GROUNDED_ON]->(:Concept)  / (:Fact)
(:Question)-[:HAS_CHOICE]->(:Choice)
(:Question)-[:HAS_ANSWER]->(:Answer)
(:Question)-[:HAS_VERIFICATION]->(:VerificationResult)
(:Question)-[:HAS_REFINEMENT]->(:RefinementLog)
(:Question)-[:OF_TYPE]->(:QuestionType)
(:Question)-[:HAS_DIFFICULTY]->(:Difficulty)
(:Question)-[:HAS_BLOOM_LEVEL]->(:BloomLevel)
```

每個 case 開跑前 runner 會呼叫 `reset_to_baseline()` 清除這些工作節點，
**保留** A/B/C 三個子圖。

## ⚠️ 重要：與其他 scenario 共用 database

`gias.toml` 的 `actions` / `blackboard` database 是全域設定，本模組與
`experiment1` / `navigation_scenario` 共用同一個 Neo4j database，
**後執行的 seed 會覆寫前一個**。
切換 scenario 時請重新執行對應的 `seed_actions` 和 `seed_blackboard`。

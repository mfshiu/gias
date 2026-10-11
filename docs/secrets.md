# 機密設定（API 金鑰、密碼、黑板金鑰）

`gias.toml` 已在 `.gitignore` 中，但檔案本身仍可能隨著複製、分享或備份外流。機密不要直接寫在設定檔裡：
寫成 `${環境變數}`，由執行環境提供實際值。

---

## 1. 在設定檔中參照環境變數

`get_agent_config()`（`src/app_helper.py`）載入設定時，把字串中的 `${VAR}` 換成環境變數 `VAR` 的值：

```toml
[broker.mqtt01]
password = "${GIAS_MQTT_PASSWORD}"

[llm.openai]
api_key = "${OPENAI_API_KEY}"     # 或整行省略：沒有 api_key 時讀 OPENAI_API_KEY

[kg.neo4j]
password = "${NEO4J_PASSWORD}"

[blackboard.access]
token = "${GIAS_BLACKBOARD_TOKEN}"
```

完整範本：repo 根目錄的 `gias.example.toml`（不含任何機密，可放進版本控制）。

載入時的提醒（每個 process、每個設定檔只出現一次，不輸出值）：

| 情況 | 訊息 |
|------|------|
| 機密以明文寫在檔案中（鍵名為 `api_key`、`password`、`token`、`secret`、`keyfile_password`，值不是 `${...}`） | `... stores secrets in plain text: llm.openai.api_key, ...` |
| 參照的環境變數沒有設定（換成空字串） | `... references unset environment variables: OPENAI_API_KEY` |

---

## 2. 設定環境變數（Windows）

只對目前的 PowerShell 視窗：

```powershell
$env:OPENAI_API_KEY = "<金鑰>"
```

對目前使用者永久生效（開新視窗後才看得到）：

```powershell
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "<金鑰>", "User")
```

環境變數仍存在於使用者設定中，但不會出現在專案目錄、版本控制或專案備份裡。以服務方式執行時，設定在服務帳號的環境變數中。

---

## 3. 從現有的 gias.toml 搬出機密

1. 把 `gias.toml` 中的 `password`、`api_key` 改成 `${...}` 參照（參考 `gias.example.toml`）
2. 依 §2 設定對應的環境變數
3. 啟動任一元件，確認不再出現「stores secrets in plain text」的提醒
4. 曾以明文存放、或可能已外流的金鑰與密碼，應在供應商（OpenAI、Neo4j、MQTT broker）處更換

其他保護：

- 限制 `gias.toml` 的檔案權限，只讓執行 GIAS 的帳號讀取
- `bak_gias_src.bat` 只備份 `src`，不含根目錄的 `gias.toml`；自行備份專案時同樣排除 `gias.toml`

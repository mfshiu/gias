# 開發流程

## 1. 用 git 保存進度，不用 7z 備份

`_bak/*.7z` 與 `_bak/src-*` 這類備份無法比較差異、無法追溯是誰為什麼改，也容易與 repo 中的程式混淆
（搜尋程式碼時會搜到舊版）。改用 git：

| 需求 | 做法 |
|------|------|
| 開始一項修改 | 從 `main` 開分支：`git switch -c fix/L-01-env-events` |
| 隨時保存進度 | 在分支上 commit（之後可以 `git rebase -i` 整理再合併） |
| 暫時擱置未完成的修改 | `git stash push -m "說明"`，之後 `git stash pop` |
| 標記里程碑（例：論文版本、實驗用的版本） | `git tag -a v0.79 -m "說明"`，`git push origin v0.79` |
| 取回舊版 | `git switch --detach v0.79`，或另開工作目錄：`git worktree add ../gias-v0.79 v0.79` |
| 比較兩個版本 | `git diff v0.79..main -- src/` |

既有的 `_bak/` 已在 `.gitignore` 中；確認需要的版本都已在 git 裡（必要時以 tag 標記）後，可把 `_bak/` 移到 repo 以外的位置封存。

---

## 2. Commit 訊息

第一行用祈使句說明這個 commit 做了什麼（不是「改了哪個檔案」），對應問題清單時在後面加上編號；
需要時空一行再寫內文，說明為什麼要這樣改、有哪些取捨：

```
Fix replanning after a closed zone: L-01

The blackboard watcher events never reached the monitor, so ...
```

不要使用 `..`、`aaa`、`update`、`fix`、`wip` 這類訊息。

`.githooks/commit-msg` 會擋下過短或不具描述性的第一行（少於 10 個位元組、只有標點或數字、
重複同一個字元、只是 wip / update / fix / test / tmp 等佔位詞）；Merge、Revert、`fixup!` / `squash!` 不檢查。
每個 clone 啟用一次：

```bash
git config core.hooksPath .githooks
```

---

## 3. 日誌

- 執行期的程式（代理、規劃、監測、黑板、KG 存取）用 logger，不用 `print`：模組可用 `init_logging()` 回傳的
  `gias` logger，或 `logging.getLogger(__name__)`
- `init_logging()` 讓 `gias` 與 `src.*`、`navigation_scenario.*` 的模組 logger 共用同一個輸出與等級，
  以環境變數 `LOG_LEVEL`（`VERBOSE` / `DEBUG` / `INFO` / `WARNING` / `ERROR`，預設 `DEBUG`）統一控制；
  輸出會遮罩 api_key、password 等欄位
- `print` 只用在命令列的輸出：啟動腳本與工具（`src/run_*.py`、`navigation_scenario/*runner*.py`、seed、`src/tools/`）、
  BlackboardAgent 的啟動摘要、單次模式最後給使用者的回覆

---

## 4. 環境與測試

安裝（Python 3.14；AgentFlow 由 GitHub 指定的 commit 安裝，本機改以 editable 方式開發 AgentFlow 時可略過該行）：

```bash
python -m pip install -r requirements-dev.txt
```

離線測試（不連 Neo4j、MQTT broker 或 LLM）：

```bash
python -m pytest tests -m "not integration" -o addopts="" -o log_cli=false
```

- 不需要 `gias.toml`：沒有時測試改用內建的最小設定（mock LLM），需要真實服務的測試會略過
- 有 `gias.toml` 時部分測試會讀取它（可由 `gias.example.toml` 複製）；設定與機密見 `docs/secrets.md`
- `-m integration` 的測試需要真實服務

---

## 5. CI

`.github/workflows/tests.yml`：push 到 `main`、開 pull request 或手動觸發時，在 Ubuntu 與 Windows（Python 3.14）
以 `requirements-dev.txt` 安裝相依套件並執行離線測試。

更新相依套件時同步修改 `requirements.txt`（例：AgentFlow 的 commit）；CI 會以新版本驗證。

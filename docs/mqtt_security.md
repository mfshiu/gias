# MQTT 連線安全（TLS、帳號、ACL）

GIAS 的元件全部經 MQTT broker 溝通。預設的 broker 設定是明文連線，帳密與訊息都可被竊聽或偽造。
本文件說明如何讓 broker 只接受加密、具名的連線，並限制可使用的 topic。

搭配 [`blackboard_agent.md`](blackboard_agent.md) §4 的黑板金鑰（S-01）：TLS 保護傳輸，金鑰限制誰能下黑板命令。

---

## 1. 目標設定

| 項目 | 設定 |
|------|------|
| 傳輸加密 | broker 開 TLS listener（例：8883），GIAS 以 TLS 連線並驗證 broker 憑證 |
| 身分 | broker 關閉匿名連線，只接受密碼檔中的帳號；GIAS 元件共用一個服務帳號 |
| 權限 | ACL：GIAS 服務帳號可讀寫全部 topic；其他帳號只能使用明確列出的 topic |

範例檔在 `deploy/mosquitto/`：`mosquitto_tls.conf`、`gias_acl.conf`。

---

## 2. 憑證

以自建 CA 為例（正式環境可改用組織的 CA）。broker 憑證的 SAN 必須包含 GIAS 連線用的主機名稱：

```bash
# CA
openssl req -x509 -newkey rsa:4096 -days 3650 -nodes -keyout ca.key -out ca.crt -subj "/CN=GIAS MQTT CA"

# broker 憑證（SAN 換成實際主機名稱與 IP）
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/CN=mqtt.example.org"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -out server.crt \
  -extfile <(printf "subjectAltName=DNS:mqtt.example.org,DNS:localhost,IP:127.0.0.1")
```

`ca.key` 只留在簽發憑證的機器上；broker 需要 `ca.crt`、`server.crt`、`server.key`，GIAS 只需要 `ca.crt`。

---

## 3. 帳號與 broker 設定

```bash
mosquitto_passwd -c "C:/Program Files/mosquitto/gias_passwd" gias
```

把 `deploy/mosquitto/mosquitto_tls.conf` 與 `gias_acl.conf` 複製到 Mosquitto 目錄並調整路徑，以該設定啟動 broker：

```bash
mosquitto -c "C:/Program Files/mosquitto/mosquitto_tls.conf"
```

ACL 注意事項：GIAS 的 topic 是單層名稱（`info.request`、`blackboard.control`、`intention.status`…，不含 `/`），
MQTT 的 `+` / `#` 無法依前綴篩選，所以 GIAS 服務帳號開放全部 topic；其他帳號只能列出確切的 topic 名稱。

---

## 4. GIAS 設定（`gias.toml`）

```toml
[broker.mqtt01]
broker_type = "mqtt"
host = "mqtt.example.org"        # 與 broker 憑證的 SAN 一致
port = 8883
username = "gias"
password = "<密碼>"

[broker.mqtt01.tls]
ca_certs = "C:/certs/gias/ca.crt"  # 不設則用系統的 CA
# certfile = "C:/certs/gias/client.crt"   # broker 開 require_certificate 時
# keyfile = "C:/certs/gias/client.key"
# insecure = true                         # 不驗證主機名稱，只限測試
```

`tls = true` 表示使用系統 CA 驗證 broker。TLS 選項由 AgentFlow 的 MQTT broker 處理（`tls_set`，強制驗證 broker 憑證）；
憑證檔讀不到或選項有誤時，元件啟動失敗，不會退回明文連線。

---

## 5. 確認

- IntentionalAgent 與 BlackboardAgent 啟動時會檢查 broker 設定（`src/security.py`）：沒有 TLS 或沒有帳號時記錄提醒
  （連到 localhost 記為 INFO，連到其他主機記為 WARNING）；設定完成後不再出現
- 以 Mosquitto 用戶端測試：
  ```bash
  mosquitto_sub -h mqtt.example.org -p 8883 --cafile ca.crt -u gias -P "<密碼>" -t intention.status -v
  ```
  不帶帳號、或連明文埠，應被拒絕

---

## 6. 限制

- 所有元件共用一個服務帳號：任一元件或其設定外洩時，持有者可使用全部 topic；依角色分帳號尚未實作
- 黑板金鑰（S-01）與 MQTT 密碼都存在 `gias.toml`，檔案本身的保護見 S-03

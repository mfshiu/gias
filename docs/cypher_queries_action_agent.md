# Cypher 查詢：Action 與 Agent

專案中 Action / Agent 的節點與關係結構可參考 `tests/seed_actions_with_embeddings/seed_actions_and_agents.py`。  
以下查詢可在 **Neo4j Browser** 或透過 `kg.query()` 使用。

---

## 圖形顯示（Neo4j Browser）

在 Neo4j Browser 要**以圖形顯示**結果，查詢必須 **RETURN 節點或關係變數**（例如 `a`、`ag`、`r`），不要只 RETURN 純屬性（例如 `a.name`）。  
下面「圖形版」查詢可直接在 Browser 執行，結果會以節點與連線顯示。

---

## 節點與關係不同顏色（GRASS 樣式）

Neo4j Browser 用 **GRASS（圖形樣式表）** 依 **節點 Label** 與 **關係 Type** 設定顏色與大小。

### 如何套用

1. 在 Neo4j Browser 執行任一個圖形查詢（例如 G3）。
2. 在結果區上方或右側找到 **「⋯」或「Style」**，點開圖形樣式。
3. 若可**編輯樣式**：把下面整段貼上，取代現有內容後儲存。
4. 或輸入指令 **`:style`** 開啟樣式編輯器，貼上下面內容後套用。

### 建議樣式（貼上即用）

不同 Label 用不同顏色，關係類型也可區分顏色：

```grass
node {
  diameter: 40px;
  color: #A5ABB6;
  border-color: #9AA1AC;
  border-width: 2px;
  text-color-internal: #FFFFFF;
  font-size: 10px;
  caption: '{id}';
}

node.Agent {
  color: #4A90D9;
  border-color: #2E6BB2;
  caption: '{name}';
}

node.Action {
  color: #50C878;
  border-color: #3A9B5C;
  caption: '{name}';
}

node.Param {
  color: #E8A838;
  border-color: #C48920;
  caption: '{key}';
}

node.Topic {
  color: #B565A7;
  border-color: #8B4A80;
  caption: '{name}';
}

node.MessageSchema {
  color: #E07C5C;
  border-color: #B85C40;
  caption: '{name}';
}

relationship {
  color: #A5ABB6;
  shaft-width: 1px;
  font-size: 8px;
  padding: 3px;
  text-color-external: #000000;
  text-color-internal: #FFFFFF;
  caption: '{type}';
}

relationship.IMPLEMENTS {
  color: #4A90D9;
  shaft-width: 2px;
}

relationship.HAS_PARAM {
  color: #C48920;
}

relationship.REQUESTS {
  color: #6B5B95;
}

relationship.RESPONDS {
  color: #88B04B;
}
```

### 顏色對照（可自改 hex）

| Label         | 顏色說明 | 色碼     |
|---------------|----------|----------|
| Agent         | 藍       | `#4A90D9` |
| Action        | 綠       | `#50C878` |
| Param         | 黃橙     | `#E8A838` |
| Topic         | 紫       | `#B565A7` |
| MessageSchema | 橘紅     | `#E07C5C` |

若節點沒有 `name` 或 `id`，Browser 可能顯示空白；可把該 label 的 `caption` 改成 `'{id}'` 或其它屬性名。

---

## 圖形版查詢（直接可畫圖）

### G1. 所有 Action 節點（圖形）

```cypher
MATCH (a:Action)
RETURN a
```

### G2. 所有 Agent 節點（圖形）

```cypher
MATCH (ag:Agent)
RETURN ag
```

### G3. Agent → IMPLEMENTS → Action（圖形，誰實作誰）

```cypher
MATCH (ag:Agent)-[r:IMPLEMENTS]->(a:Action)
RETURN ag, r, a
```

### G4. 某個 Agent 與其實作的所有 Action（圖形）

```cypher
MATCH (ag:Agent {id: "guide_agent"})-[r:IMPLEMENTS]->(a:Action)
RETURN ag, r, a
```

### G5. Action 與其參數 Param（圖形）

```cypher
MATCH (a:Action)-[r:HAS_PARAM]->(p:Param)
RETURN a, r, p
```

### G6. 某個 Action 與其 Param（圖形）

```cypher
MATCH (a:Action {name: "LocateExhibit"})-[r:HAS_PARAM]->(p:Param)
RETURN a, r, p
```

### G7. Action 的 Request/Response Topic（圖形）

```cypher
MATCH (a:Action)-[r1:REQUESTS]->(treq:Topic)
MATCH (a)-[r2:RESPONDS]->(tresp:Topic)
RETURN a, r1, treq, r2, tresp
```

### G8. 單一 Action 的 Topic（圖形）

```cypher
MATCH (a:Action {name: "LocateExhibit"})
OPTIONAL MATCH (a)-[r1:REQUESTS]->(treq:Topic)
OPTIONAL MATCH (a)-[r2:RESPONDS]->(tresp:Topic)
RETURN a, r1, treq, r2, tresp
```

### G9. 大圖：Agent + Action + Param（圖形，節點較多）

```cypher
MATCH (ag:Agent)-[r_impl:IMPLEMENTS]->(a:Action)
OPTIONAL MATCH (a)-[r_param:HAS_PARAM]->(p:Param)
RETURN ag, r_impl, a, r_param, p
```

### G10. 依名稱搜尋 Action（圖形）

```cypher
MATCH (a:Action)
WHERE a.name STARTS WITH "Locate"
RETURN a
```

---

## 表格版查詢（僅欄位，適合程式或匯出）

### 1. 查詢所有 Action（基本）

```cypher
MATCH (a:Action)
RETURN a.name AS name, a.description AS description, a.version AS version
ORDER BY a.name
```

---

### 2. 查詢所有 Agent（基本）

```cypher
MATCH (ag:Agent)
RETURN ag.id AS id, ag.name AS name, ag.description AS description, ag.status AS status, ag.version AS version
ORDER BY ag.id
```

---

### 3. 查詢 Action 與其所屬 Agent（誰實作哪個 Action）

```cypher
MATCH (ag:Agent)-[:IMPLEMENTS]->(a:Action)
RETURN ag.id AS agent_id, ag.name AS agent_name, a.name AS action_name, a.description AS action_description
ORDER BY ag.id, a.name
```

---

### 4. 查詢某個 Agent 實作的所有 Action

```cypher
MATCH (ag:Agent {id: $agent_id})-[:IMPLEMENTS]->(a:Action)
RETURN a.name AS action_name, a.description AS description, a.version AS version
ORDER BY a.name
```

參數範例：`{"agent_id": "guide_agent"}`

---

### 5. 查詢某個 Action 的參數（HAS_PARAM）

```cypher
MATCH (a:Action {name: $action_name})-[:HAS_PARAM]->(p:Param)
RETURN p.key AS key, p.name AS name, p.description AS description, p.type AS type, p.required AS required, p.enum AS enum, p.example AS example
ORDER BY p.required DESC, p.key
```

參數範例：`{"action_name": "LocateExhibit"}`

---

### 6. 查詢所有 Action 及其參數（一對多）

```cypher
MATCH (a:Action)
OPTIONAL MATCH (a)-[:HAS_PARAM]->(p:Param)
WITH a, collect({key: p.key, name: p.name, type: p.type, required: p.required}) AS params
RETURN a.name AS action_name, a.description AS description, params
ORDER BY a.name
```

---

### 7. 查詢所有 Agent 及其實作的 Action（一對多）

```cypher
MATCH (ag:Agent)
OPTIONAL MATCH (ag)-[:IMPLEMENTS]->(a:Action)
WITH ag, collect(a.name) AS action_names
RETURN ag.id AS agent_id, ag.name AS agent_name, ag.status AS status, action_names
ORDER BY ag.id
```

---

### 8. 查詢 Action 的 Request/Response Topic（PubSub 契約）

```cypher
MATCH (a:Action {name: $action_name})
OPTIONAL MATCH (a)-[:REQUESTS]->(treq:Topic)
OPTIONAL MATCH (a)-[:RESPONDS]->(tresp:Topic)
RETURN a.name AS action_name,
       treq.name AS request_topic,
       tresp.name AS response_topic
```

參數範例：`{"action_name": "LocateExhibit"}`

---

### 9. 統計：每個 Agent 的 Action 數量

```cypher
MATCH (ag:Agent)-[:IMPLEMENTS]->(a:Action)
RETURN ag.id AS agent_id, ag.name AS agent_name, count(a) AS action_count
ORDER BY action_count DESC
```

---

### 10. 依名稱搜尋 Action（前綴或包含）

```cypher
// 前綴
MATCH (a:Action)
WHERE a.name STARTS WITH $prefix
RETURN a.name, a.description
ORDER BY a.name
// 參數例: {"prefix": "Locate"}

// 包含
MATCH (a:Action)
WHERE toLower(a.name) CONTAINS toLower($keyword)
RETURN a.name, a.description
ORDER BY a.name
// 參數例: {"keyword": "Exhibit"}
```

---

## 節點與關係速查

| 節點 Label   | 常用屬性 |
|-------------|----------|
| `Action`    | name, description, description_embedding, timeout_ms, retries, idempotent, version |
| `Agent`     | id, name, description, status, version |
| `Param`     | key, name, description, type, required, enum, example |
| `Topic`     | name, transport, scope, version, pattern |
| `MessageSchema` | name, content_type, required_headers, example_json, version |

| 關係類型   | 方向 | 說明 |
|------------|------|------|
| IMPLEMENTS | Agent → Action | Agent 實作該 Action |
| HAS_PARAM  | Action → Param | Action 的參數定義 |
| REQUESTS   | Action → Topic | 請求用的 Topic |
| RESPONDS   | Action → Topic | 回應用的 Topic |
| HAS_SCHEMA | Topic → MessageSchema | Topic 的訊息 Schema |

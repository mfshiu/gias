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

---

# Blackboard 圖譜查詢

Blackboard 儲存展場即時脈絡（Zones、POI、Booth、Agent 狀態與位置、Task 等），詳見 `tests/seed_blackboard/seed_blackboard.py`。

---

## 圖形版查詢（Neo4j Browser 可畫圖）

### B-G1. 所有節點與關係（小規模時使用）

```cypher
MATCH (n)
OPTIONAL MATCH (n)-[r]->(m)
RETURN n, r, m
LIMIT 300
```

### B-G2. Zones、POI、Booth 與其位置關係

```cypher
MATCH (loc)-[r:LOCATED_IN]->(z:Zone)
RETURN loc, r, z
```

### B-G3. POI/Booth 之間的連接路徑

```cypher
MATCH (a)-[r:CONNECTED_TO]->(b)
RETURN a, r, b
```

### B-G4. Agent 與其 Skill

```cypher
MATCH (ag:Agent)-[r:HAS_SKILL]->(sk:Skill)
RETURN ag, r, sk
```

### B-G5. Agent 的目前位置與狀態

```cypher
MATCH (ag:Agent)
OPTIONAL MATCH (ag)-[rp:CURRENT_POSITION]->(pos)
OPTIONAL MATCH (ag)-[rs:CURRENT_STATE]->(st:State)
RETURN ag, rp, pos, rs, st
```

### B-G6. Zone 的人潮狀態

```cypher
MATCH (z:Zone)-[r:CURRENT_STATE]->(st:State)
RETURN z, r, st
```

### B-G7. Task 與其相關節點（起點、終點、狀態、技能）

```cypher
MATCH (t:Task)
OPTIONAL MATCH (t)-[r1:START_LOCATION]->(start)
OPTIONAL MATCH (t)-[r2:TARGET_LOCATION]->(target)
OPTIONAL MATCH (t)-[r3:HAS_STATUS]->(st:State)
OPTIONAL MATCH (t)-[r4:REQUIRES_SKILL]->(sk:Skill)
RETURN t, r1, start, r2, target, r3, st, r4, sk
```

---

## 表格版查詢（欄位輸出）

### B1. 列出所有 Zone

```cypher
MATCH (z:Zone)
RETURN z.name AS zone_name
ORDER BY z.name
```

### B2. 列出所有 POI

```cypher
MATCH (p:POI)
RETURN p.id AS id, p.name AS name
ORDER BY p.id
```

### B3. 列出所有 Booth

```cypher
MATCH (b:Booth)
RETURN b.id AS id, b.exhibitor AS exhibitor
ORDER BY b.id
```

### B4. POI/Booth 屬於哪個 Zone

```cypher
MATCH (loc)-[:LOCATED_IN]->(z:Zone)
RETURN labels(loc)[0] AS type, loc.id AS id, coalesce(loc.name, loc.exhibitor) AS name, z.name AS zone
ORDER BY zone, type, id
```

### B5. 連接路徑（距離）

```cypher
MATCH (a)-[r:CONNECTED_TO]->(b)
WHERE id(a) < id(b)
RETURN a.id AS from_id, b.id AS to_id, r.distance AS distance
ORDER BY from_id, to_id
```

### B6. 所有 Agent

```cypher
MATCH (ag:Agent)
RETURN ag.agent_id AS agent_id, ag.type AS type
ORDER BY ag.agent_id
```

### B7. Agent 擁有的 Skill

```cypher
MATCH (ag:Agent)-[:HAS_SKILL]->(sk:Skill)
RETURN ag.agent_id AS agent_id, sk.name AS skill
ORDER BY agent_id
```

### B8. Agent 目前位置與狀態

```cypher
MATCH (ag:Agent)
OPTIONAL MATCH (ag)-[:CURRENT_POSITION]->(pos)
OPTIONAL MATCH (ag)-[:CURRENT_STATE]->(st:State)
RETURN ag.agent_id AS agent_id,
       coalesce(pos.id, pos.name) AS position,
       st.status_name AS status
ORDER BY agent_id
```

### B9. Zone 人潮狀態

```cypher
MATCH (z:Zone)
OPTIONAL MATCH (z)-[:CURRENT_STATE]->(st:State)
RETURN z.name AS zone, st.status_name AS crowd_status
ORDER BY zone
```

### B10. 所有 Task

```cypher
MATCH (t:Task)
OPTIONAL MATCH (t)-[:HAS_STATUS]->(st:State)
OPTIONAL MATCH (t)-[:START_LOCATION]->(start)
OPTIONAL MATCH (t)-[:TARGET_LOCATION]->(target)
OPTIONAL MATCH (t)-[:REQUIRES_SKILL]->(sk:Skill)
RETURN t.task_id AS task_id,
       t.type AS type,
       t.priority AS priority,
       st.status_name AS status,
       start.id AS start_id,
       target.id AS target_id,
       sk.name AS required_skill
ORDER BY task_id
```

---

## Blackboard 節點與關係速查

| 節點 Label | 常用屬性 |
|------------|----------|
| Zone | name |
| POI | id, name |
| Booth | id, exhibitor |
| Agent | agent_id, type |
| Skill | name |
| State | status_name |
| Task | task_id, type, priority |

| 關係類型 | 方向 | 說明 |
|----------|------|------|
| LOCATED_IN | POI/Booth → Zone | 位於哪個區域 |
| CONNECTED_TO | POI/Booth ↔ POI/Booth | 連接路徑（有 distance） |
| HAS_SKILL | Agent → Skill | Agent 擁有的能力 |
| CURRENT_POSITION | Agent → POI/Booth | Agent 目前位置 |
| CURRENT_STATE | Agent/Zone → State | 目前狀態 |
| HAS_STATUS | Task → State | 任務狀態 |
| REQUIRES_SKILL | Task → Skill | 任務所需能力 |
| START_LOCATION | Task → POI/Booth | 任務起點 |
| TARGET_LOCATION | Task → POI/Booth | 任務終點 |

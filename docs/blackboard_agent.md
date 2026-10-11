# BlackboardAgent 設定與領域擴充

BlackboardAgent（`src/blackboard/agent.py`）監看 Blackboard KG 的變動並通知訂閱者，也接收觀察者的觀察結果。
核心程式不寫死任何領域；展場（venue）的規則以預設集提供，其他領域可用設定或建構參數替換。

---

## 1. 設定（`gias.toml`）

```toml
[blackboard]
poll_interval_sec = 2.0        # 輪詢週期：偵測直接寫入 Neo4j 的變動（經 BlackboardAgent 寫入的變動會立即偵測）
broadcast_events = true        # 把每個變動廣播到 blackboard.event.*；false 時只掃描有人訂閱的 Label
projections = ["venue"]        # 觀察投影預設集；未設定為 ["venue"]，[] 表示不投影

# 監看目標；未設定時使用內建的 Agent、Zone、Task、POI、Booth
[[blackboard.watch_targets]]
label = "Room"
id_property = "id"             # 預設 "id"
properties = ["temperature", "occupancy"]
relationships = ["IN_BUILDING"]
```

建構參數 `BlackboardAgent(config, projections=[...], watch_targets=[...], broadcast_events=...)` 優先於設定。

---

## 2. 觀察投影（`src/blackboard/projection.py`）

觀察者以 `blackboard.control {command: observe}` 回報觀察結果時，BlackboardAgent 會：

1. 記錄 `(:Observation)` 與 `(:ObservedEntity)`（所有領域相同）
2. 依投影規則把實體同步到領域節點，供行動者代理查詢；單筆失敗只記 warning
3. 發佈 `Observation/<observer_id>/<observation_type>` 事件

投影規則是 `ObservationProjection(observation_type, entity_type, build)`：`build(entity)` 回傳 `(cypher, params)`，
資料不足時回傳 `None`（略過）。

內建預設集 `venue`：

| 觀察類型 | 實體類型 | 寫入 |
|----------|----------|------|
| `pedestrian_flow` | `Zone` | 把 Zone 的 `CURRENT_STATE` 換成 `properties.crowd_level` 對應的 State |
| `facility_event` | `Booth` | 設定 Booth 的 `status` |

其他領域：

```python
from src.blackboard import ObservationProjection, register_projection_preset

register_projection_preset("office", [
    ObservationProjection(
        "room_sensor", "Room",
        lambda e: ("MATCH (r:Room {id: $id}) SET r.temperature = $t",
                   {"id": e["entity_id"], "t": (e.get("properties") or {}).get("temperature")}),
    ),
])
```

再於設定中指定 `projections = ["office"]`，或直接傳入 `projections=`。

---

## 3. 事件 topic

| 事件 | topic | metadata |
|------|-------|----------|
| 節點屬性變動 | `<Label>/<id>/<property>` | `label`、`node_id`、`property` |
| 節點新增 / 刪除 | `<Label>/<id>/*` | `label`、`node_id` |
| 關係新增 / 刪除 | `<SourceLabel>/<REL>/<TargetLabel>` | `source_label`、`source_id`、`rel_type`、`target_label`、`target_id` |
| 觀察 | `Observation/<observer_id>/<observation_type>` | `observer_id`、`observation_type` |

訂閱 pattern 以 `/` 分段，`*` 比對任一段（例：`Zone/*/*`）。IntentionalAgent 依 `DomainProfile.env_subscriptions`
訂閱，並以同一組 pattern 判斷事件是否與計畫有關（見 `monitoring_protocol.md` §7.1）。

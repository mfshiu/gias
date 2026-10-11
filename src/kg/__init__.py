# src/kg/__init__.py
"""
知識圖譜（Neo4j）存取。

- adapter_neo4j.Neo4jBoltAdapter：連線、讀寫（唯讀查詢在唯讀交易中執行）、重試、向量索引
- action_store.ActionStore：Action KG 的查詢（參數、輸出、前置條件、向量搜尋；有快取）
- cache.TTLCache：KG 查詢結果的時效快取

Blackboard KG 由 BlackboardAgent 持有連線；其他元件經 blackboard.control 存取（src/blackboard/client.py）。
"""

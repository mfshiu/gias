# 定義「任務輸出格式」：強制 JSON schema（或 Pydantic）
# 例如 IntentCandidate{ goal, slots, confidence, evidence_ids }
# 讓不同任務能有一致的輸出格式，方便後續處理
# 也方便做結果驗證與錯誤處理
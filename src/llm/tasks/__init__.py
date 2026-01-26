# 所有代理都只呼叫這層
# 每個 task：
#   準備 input（含 KG/RAG evidence）
#   取 prompt 模板
#   呼叫 LLMClient.json()
#   schema 驗證失敗就做「修復提示 → 再試一次」

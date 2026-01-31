# test/query_decomposition/qd03.py
# GIAS Query Decomposition Validation Script
import os
import json
import time
from dotenv import load_dotenv
from openai import OpenAI

# ==========================================
# 1. 設定與初始化
# ==========================================
load_dotenv()
# 請確保您的 .env 檔案中有 OPENAI_API_KEY
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") 

# 如果沒有讀取到 Key，簡單防呆
if not OPENAI_API_KEY:
    print("⚠️ Warning: No API Key found. Please set OPENAI_API_KEY in .env")

client = OpenAI(api_key=OPENAI_API_KEY)
MODEL_ID = "gpt-4o-mini" 

# ==========================================
# 2. 核心函數：Query Decomposition
# ==========================================
def llm_query_decomposer(user_query):
    """
    Step 1: Query Decomposition
    Input: 複雜的自然語言字串
    Output: 拆解後的 List[str]
    """
    prompt = f"""
    You are a command parser for a smart assistant system (GIAS).
    Break down the following user query into a list of independent, executable sub-commands.
    
    Rules:
    1. Split compound commands (e.g., "A and B") into separate items.
    2. Remove polite filler words (e.g., "please", "help me").
    3. Keep context if necessary for the command to make sense.
    4. Output ONLY a valid JSON list of strings.
    
    User Query: "{user_query}"
    """
    
    try:
        response = client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": "Output only JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0 # 設為 0 以確保穩定性
        )
        
        # 解析 JSON 字串
        content = response.choices[0].message.content.strip()
        # 清理可能出現的 markdown 標記 (```json ... ```)
        if content.startswith("```"):
            content = content.replace("```json", "").replace("```", "")
            
        decomposed_list = json.loads(content)
        return decomposed_list

    except Exception as e:
        print(f"Error parsing: {e}")
        # Fallback: 如果 LLM 失敗，回傳原始字串作為單一元素的 List
        return [user_query]

# ==========================================
# 3. 測試資料
# ==========================================
test_queries = [
    "幫我打開客廳的落地燈。",
]

# ==========================================
# 4. 執行批次驗證
# ==========================================
print(f"🚀 Starting GIAS Query Decomposition Validation (Model: {MODEL_ID})\n")

for i, query in enumerate(test_queries, 1):
    print(f"--- [Case {i}] ---")
    print(f"📥 Input: {query}")
    
    # 呼叫 LLM
    start_time = time.time()
    results = llm_query_decomposer(query)
    end_time = time.time()
    
    print(f"📤 Decomposed: {results}")
    print(f"⏱️ Time: {end_time - start_time:.2f}s")
    
    # 模擬下一步 (Step 2 Hybrid Search)
    print("⚙️  Next Steps:")
    for sub_query in results:
        print(f"   --> Parallel Search (Vector + KG) for: '{sub_query}'")
    print("\n")

print("✅ Validation Complete.")

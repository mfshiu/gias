import os
import json
import time
from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI

# ==========================================
# 1. 設定與初始化
# ==========================================
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7689")
NEO4J_AUTH = None # 根據你的設定，No Auth Mode

if not OPENAI_API_KEY:
    print("⚠️ Warning: No API Key found.")

client = OpenAI(api_key=OPENAI_API_KEY)
driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

MODEL_ID = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-small"


# ==========================================
# 2. 輔助函數 (Embedding & Decomposition)
# ==========================================
def get_embedding(text):
    """將文字轉為 1536 維向量"""
    text = text.replace("\n", " ")
    return client.embeddings.create(input=[text], model=EMBEDDING_MODEL).data[0].embedding


def llm_query_decomposer(user_query):
    """Step 1: 將複雜語句拆解為單一意圖"""
    prompt = f"""
    You are the GIAS Command Parser.
    Split the user query into a list of independent sub-commands.
    Remove polite words. Keep context.
    Output ONLY a valid JSON list of strings.
    
    User Query: "{user_query}"
    """
    try:
        response = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"): 
            content = content.replace("```json", "").replace("```", "")
        return json.loads(content)
    except Exception:
        return [user_query]

# ==========================================
# 3. 核心函數：向量檢索 (Vector Search)
# ==========================================
def find_action_by_vector(tx, user_sub_command):
    """
    Step 2: 利用向量相似度在 KG 中尋找最匹配的 Action
    """
    # 1. 將使用者的自然語言指令轉為向量
    command_vector = get_embedding(user_sub_command)
    
    # 2. Cypher 查詢：計算 Cosine Similarity
    # 注意：這裡假設 Neo4j 5.x+ 支援 vector.similarity.cosine
    # 如果資料量大，建議建立 Vector Index，這裡演示遍歷計算 (Brute Force)
    query = """
    MATCH (a:Action)
    WHERE a.vector IS NOT NULL
    WITH a, vector.similarity.cosine(a.vector, $command_vector) AS score
    WHERE score > 0.40  // 設定一個相似度門檻
    RETURN a.name AS action_name, a.behavior AS behavior, score
    ORDER BY score DESC
    LIMIT 1
    """
    
    result = tx.run(query, command_vector=command_vector).single()
    
    if result:
        return {
            "action": result["action_name"],
            "behavior": result["behavior"],
            "score": result["score"]
        }
    return None

def get_action_slots(tx, action_name):
    """
    獲取該 Action 關聯的 Slots 定義 (透過 REQUIRES 關係)
    """
    query = """
    MATCH (a:Action {name: $action_name})-[r:REQUIRES]->(s:Slot)
    RETURN s.name AS slot_name, r.reason AS reason
    """
    results = tx.run(query, action_name=action_name)
    return [{"name": record["slot_name"], "reason": record["reason"]} for record in results]

# ==========================================
# 4. 核心函數：模擬呼叫 (Simulated Execution)
# ==========================================
def extract_parameters(sub_command, action_info, slots_info):
    """
    Step 3: 根據找到的 Action 定義，讓 LLM 提取參數
    """
    if not slots_info:
        return {}

    prompt = f"""
    You are the GIAS Slot Filler.
    
    Target Action: "{action_info['action']}"
    Action Behavior: "{action_info['behavior']}"
    Original Command: "{sub_command}"
    
    Required Slots:
    {json.dumps(slots_info, ensure_ascii=False)}
    
    Task: Extract the values for the required slots from the command.
    Output JSON only: {{ "slot_name": "extracted_value" }}
    If a slot is missing, use null.
    """
    
    response = client.chat.completions.create(
        model=MODEL_ID,
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    content = response.choices[0].message.content.strip()
    if content.startswith("```"): content = content.replace("```json", "").replace("```", "")
    return json.loads(content)

# ==========================================
# 5. 主流程 (Main Pipeline)
# ==========================================
def run_gias_pipeline(user_query):
    print(f"\n🔵 [User Input]: {user_query}")
    
    # --- 1. Decomposition ---
    sub_commands = llm_query_decomposer(user_query)
    print(f"🔸 [Decomposition]: {sub_commands}")
    
    with driver.session() as session:
        for cmd in sub_commands:
            print(f"\n   👉 Processing: '{cmd}'")
            
            # --- 2. Vector Search in KG ---
            start_t = time.time()
            match = session.execute_read(find_action_by_vector, cmd)
            
            if match:
                print(f"      ✅ Match Found in KG (Score: {match['score']:.4f})")
                print(f"         Action: {match['action']}")
                print(f"         Desc:   {match['behavior']}")
                
                # --- 3. Context-Aware Slot Filling ---
                # 撈取該 Action 需要什麼參數
                slots_schema = session.execute_read(get_action_slots, match['action'])
                
                # 讓 LLM 填空
                params = extract_parameters(cmd, match, slots_schema)
                
                # --- 4. Simulate Call ---
                print(f"      🤖 [Simulating Call]: {match['action']}({params})")
                
            else:
                print("      ❌ No suitable tool found in Knowledge Graph.")
            
            print(f"      (Time: {time.time() - start_t:.2f}s)")

# ==========================================
# 6. 執行測試
# ==========================================
if __name__ == "__main__":
    test_cases = [
        "幫我把客廳的冷氣設為26度", 
        "提醒我明天早上九點開會",
        "我想聽周杰倫的歌", # 測試模糊意圖
    ]
    
    print("🚀 GIAS Vector-Based Execution Engine Started")
    for q in test_cases:
        run_gias_pipeline(q)
    
    driver.close()
    
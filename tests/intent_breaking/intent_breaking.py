import os
import json
import time
from dotenv import load_dotenv
from openai import OpenAI

# ==========================================
# 1. 設定與初始化 (Configuration)
# ==========================================
load_dotenv()
# 從環境變數取得 OpenAI API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("錯誤：未偵測到環境變數 'OPENAI_API_KEY'。")

# 初始化 OpenAI Client
client = OpenAI(api_key=OPENAI_API_KEY)

# 指定模型 (建議使用 gpt-4o 或 gpt-4o-mini 以獲得最佳的 JSON 遵循能力)
MODEL_ID = "gpt-4o-mini"
MODEL_ID = "gpt-4o"

# 定義系統目前擁有的原子意圖 (GIAS 已定義部分)
KNOWN_ATOMIC_INTENTS = [
    "Move_To(Location)",
    "Turn(Direction)",
    "Query_DB(Key)",
    "IoT_Switch(Device_ID, State)",
    "Say(Text)"
]

# ==========================================
# 2. 核心 Prompt 與 API 呼叫
# ==========================================

def build_prompt(current_intent):
    tools_str = ", ".join(KNOWN_ATOMIC_INTENTS)
    return f"""
    You are the "GIAS Intent Decomposition Engine". 
    Break down the User Intent into immediate sub-intents (one level deep only).
    
    ### Input Data
    - **Current Intent**: "{current_intent}"
    - **Available Atomic Intents**: [{tools_str}]
    
    ### Rules
    1. One Level Only: Do not decompose recursively in your response. Only identify immediate children.
    2. Atomic Check:
       - Match "Available Atomic Intent" -> mark is_atomic: true, atomic_source: "pre_defined".
       - Specific action NOT in list -> mark is_atomic: true, atomic_source: "new_generated".
       - High-level/Needs more steps -> mark is_atomic: false.
    3. Logical Progress: Ensure the decomposition moves toward solving the problem without diverging.

    ### Output Format
    Return ONLY valid JSON.
    {{
      "parent_intent": "string",
      "sub_intents": [
        {{
          "id": "string",
          "content": "string",
          "is_atomic": boolean,
          "atomic_source": "pre_defined" | "new_generated" | null
        }}
      ],
      "relationships": [
        {{ "type": "Sequence"|"Parallel", "from_id": "...", "to_id": "..." }}
      ]
    }}
    """

def call_llm_decompose(intent):
    try:
        prompt = build_prompt(intent)
        
        # 呼叫 OpenAI API
        # 使用 response_format={ "type": "json_object" } 確保輸出為 JSON
        response = client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={ "type": "json_object" }
        )
        
        response_text = response.choices[0].message.content.strip()
        
        # 解析 JSON
        return json.loads(response_text)
    except Exception as e:
        print(f"[Error] OpenAI API 呼叫失敗: {e}")
        return None

# ==========================================
# 3. 遞迴邏輯 (Hierarchical Task Decomposition)
# ==========================================

def recursive_planner(intent, depth=0, max_depth=4):
    """
    GIAS 遞迴規劃器：層層拆解直到原子意圖
    """
    indent = "    " * depth
    prefix = "└── " if depth > 0 else "[ROOT] "
    print(f"{indent}{prefix}處理意圖: {intent}")
    
    if depth >= max_depth:
        print(f"{indent}    [!] 達到最大深度，停止拆解。")
        return

    # 取得本層拆解結果
    result_json = call_llm_decompose(intent)
    if not result_json:
        return

    sub_intents = result_json.get("sub_intents", [])
    
    for sub in sub_intents:
        content = sub['content']
        is_atomic = sub.get('is_atomic', False)
        source = sub.get('atomic_source')

        if is_atomic:
            # 葉節點 (Atomic Intent)
            marker = "🟢 [EXEC]" if source == "pre_defined" else "🔴 [NEW]"
            print(f"{indent}    {marker} {content} (Type: {source})", flush=True)
        else:
            time.sleep(0.2) # 稍微延遲避免觸發 Rate Limit
            # 複合節點 (繼續遞迴)
            recursive_planner(content, depth + 1, max_depth)
        

# ==========================================
# 4. 執行入口
# ==========================================

if __name__ == "__main__":
    root_intent = "執行 VIP 訪客接待與展示廳自動化巡檢"
    root_intent = "準備 301 會議室，下午兩點要跟客戶進行視訊提案。"
    
    print("=== GIAS 意圖拆解系統啟動 (OpenAI Mode) ===")
    print(f"[系統資訊] 使用模型: {MODEL_ID}")
    print("-" * 50)
    
    start_time = time.time()
    recursive_planner(root_intent, max_depth=5)
    
    print("-" * 50)
    print(f"=== 拆解完成，總計用時: {time.time() - start_time:.2f} 秒 ===")



import os
import json
import time
from dotenv import load_dotenv
from openai import OpenAI

# ==========================================
# 1. 設定與初始化 (Configuration)
# ==========================================
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("錯誤：未偵測到環境變數 'OPENAI_API_KEY'。")

client = OpenAI(api_key=OPENAI_API_KEY)
MODEL_ID = "gpt-4o-mini"
MODEL_ID = "gpt-4o"

# 【優化點】定義帶有說明的原子意圖
# 使用字典格式，方便 LLM 理解每個工具的物理/資訊意義
KNOWN_ATOMIC_INTENTS = {
    "Move_To(Location)": "Move to a specified location.",
    "Turn(Direction)": "Rotate to a specified direction or angle.",
    "Query_DB(Key)": "Retrieve information from the database.",
    "IoT_Switch(Device_ID, State)": "Control an IoT device’s state.",
    "Say(Text)": "Speak or communicate with humans."
}

# ==========================================
# 2. 核心 Prompt 與 API 呼叫
# ==========================================

def build_prompt(current_intent):
    # 將字典轉換為易讀的清單字串
    tools_description = "\n".join([f"- {k}: {v}" for k, v in KNOWN_ATOMIC_INTENTS.items()])
    
    return f"""
    You are the "GIAS Intent Decomposition Engine". 
    Break down the User Intent into immediate sub-intents (one level deep only).
    
    ### Input Data
    - **Current Intent**: "{current_intent}"
    - **Available Atomic Intents**: [{tools_description}]
    
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
        response = client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": "You are a specialized agent for HTN (Hierarchical Task Network) planning. Output structured JSON for intent decomposition."},
                {"role": "user", "content": prompt}
            ],
            response_format={ "type": "json_object" }
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print(f"[Error] OpenAI API 呼叫失敗: {e}")
        return None

# ==========================================
# 3. 遞迴邏輯 (Hierarchical Task Decomposition)
# ==========================================

def recursive_planner(intent, depth=0, max_depth=8):
    indent = "    " * depth
    prefix = "└── " if depth > 0 else "[ROOT] "
    print(f"{indent}{prefix}處理意圖: {intent}")
    
    if depth >= max_depth:
        print(f"{indent}    [!] 達到最大深度，停止拆解。")
        return

    result_json = call_llm_decompose(intent)
    if not result_json:
        return

    sub_intents = result_json.get("sub_intents", [])
    
    for sub in sub_intents:
        content = sub['content']
        is_atomic = sub.get('is_atomic', False)
        source = sub.get('atomic_source')

        if is_atomic:
            marker = "🟢 [EXEC]" if source == "pre_defined" else "🔴 [NEW]"
            print(f"{indent}    {marker} {content} (Type: {source})")
        else:
            time.sleep(0.1) # 避開極短時間內的高頻請求
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
    recursive_planner(root_intent, max_depth=4)
    
    print("-" * 50)
    print(f"=== 拆解完成，總計用時: {time.time() - start_time:.2f} 秒 ===")

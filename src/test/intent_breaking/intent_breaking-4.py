import os
import json
import time
from dotenv import load_dotenv
from openai import OpenAI

# ==========================================
# 1. 設定與初始化
# ==========================================
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)
MODEL_ID = "gpt-4o" 

KNOWN_ATOMIC_INTENTS = {
    "Move_To(Location)": "Robot moves to a specific location.",
    "Turn(Direction)": "Rotate to a specified orientation.",
    "Query_DB(Key)": "Retrieve data/info from the system database.",
    "IoT_Switch(Device_ID, State)": "Control IoT hardware state.",
    "Say(Text)": "Speech output for interaction."
}

# ==========================================
# 2. 核心 Prompt 設計 (支援時間與單層拆解)
# ==========================================

def build_prompt(current_intent):
    tools_description = "\n".join([f"- {k}: {v}" for k, v in KNOWN_ATOMIC_INTENTS.items()])
    
    return f"""
    You are the "GIAS Intent Decomposition Engine". 
    Break down the User Intent into immediate sub-intents (one level deep only).
    
    ### Available Atomic Intents
    {tools_description}
    
    ### Context
    - **Current Intent**: "{current_intent}"
    
    ### Rules
    1. **Time Awareness**: Only assign a `scheduled_start` if a specific, absolute time is mentioned or logically required (e.g., "14:00"). 
    2. **No Relative Time**: Do NOT use relative markers like "T-15m" or "Asap". 
    3. **Empty Value**: If a sub-intent does not have a confirmed absolute start time, set `scheduled_start` to "".
    4. **Atomic Check**: Match pre-defined tools or create "new_generated" ones.

    ### Output Format
    Return ONLY valid JSON.
    {{
      "parent_intent": "string",
      "sub_intents": [
        {{
          "id": "string",
          "content": "string",
          "is_atomic": boolean,
          "atomic_source": "pre_defined" | "new_generated" | null,
          "scheduled_start": "string (HH:MM or empty)"
        }}
      ],
      "relationships": [
        {{ "type": "Sequence"|"Parallel", "from_id": "string", "to_id": "string" }}
      ]
    }}
    """

def call_llm_decompose(intent):
    try:
        response = client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": "You are a specialized agent for Time-Aware HTN planning."},
                {"role": "user", "content": build_prompt(intent)}
            ],
            response_format={ "type": "json_object" }
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print(f"[Error] API Call failed: {e}")
        return None

# ==========================================
# 3. 遞迴邏輯 (修正：最大深度視為原子意圖)
# ==========================================

def recursive_planner(intent, depth=0, max_depth=4, scheduled_start="N/A"):
    indent = "    " * depth
    prefix = "└── " if depth > 0 else "[ROOT] "
    
    # === 核心修改：到達最大深度，不再呼叫 LLM，直接視為原子意圖 ===
    if depth >= max_depth:
        # 由於已到達深度，我們直接將其判定為一個待實現的「新原子意圖」
        print(f"{indent}[{scheduled_start}] 🔴 [NEW] {intent} (Type: leaf_forced_atomic)")
        return

    print(f"{indent}{prefix}處理意圖: {intent}")

    result_json = call_llm_decompose(intent)
    if not result_json:
        return

    sub_intents = result_json.get("sub_intents", [])
    
    for sub in sub_intents:
        content = sub['content']
        is_atomic = sub.get('is_atomic', False)
        source = sub.get('atomic_source')
        sched_time = sub.get('scheduled_start', 'N/A')

        if is_atomic:
            marker = "🟢 [EXEC]" if source == "pre_defined" else "🔴 [NEW]"
            print(f"{indent}    [{sched_time}] {marker} {content} (Type: {source})")
        else:
            time.sleep(0.1) # 避頻率限制
            # 繼續向下遞迴，並傳遞時間資訊
            recursive_planner(content, depth + 1, max_depth, sched_time)

# ==========================================
# 4. 執行
# ==========================================

if __name__ == "__main__":
    root_intent = "先去 A1 倉庫領取測試樣品並送往 301 會議室，接著在下午兩點準時為 VIP 客戶進行產品演示，演示包含投影控制與樣品解說，完成後引導客戶前往 1 樓出口並發送滿意度調查。"
    
    print("=== GIAS 意圖拆解系統啟動 (Pruning at Max Depth) ===")
    print("-" * 50)
    recursive_planner(root_intent, max_depth=4)

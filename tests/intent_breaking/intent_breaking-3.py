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
MODEL_ID = "gpt-4o"  # 建議使用 gpt-4o 以獲得更好的邏輯推理

KNOWN_ATOMIC_INTENTS = {
    "Move_To(Location)": "Robot moves to a specific location.",
    "Turn(Direction)": "Rotate to a specified orientation.",
    "Query_DB(Key)": "Retrieve data/info from the system database.",
    "IoT_Switch(Device_ID, State)": "Control IoT hardware state.",
    "Say(Text)": "Speech output for interaction."
}

# ==========================================
# 2. 核心 Prompt 設計 (加入時間約束邏輯)
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
    1. **Time Awareness**: If the intent mentions a specific time (e.g., 2:00 PM), identify which sub-intents must start at that exact time and which are preparatory steps that must be completed BEFORE.
    2. **Scheduled Start**: For each sub-intent, provide a `scheduled_start` (e.g., "14:00", "T-minus 15m", or "Asap").
    3. **Atomic Check**: Match pre-defined tools or create "new_generated" ones.

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
          "scheduled_start": "string (Specific time or relative time)"
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
                {"role": "system", "content": "You are a specialized agent for Time-Aware HTN planning. Ensure critical tasks have specific timestamps."},
                {"role": "user", "content": build_prompt(intent)}
            ],
            response_format={ "type": "json_object" }
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print(f"[Error] API Call failed: {e}")
        return None

# ==========================================
# 3. 遞迴邏輯 (顯示時間資訊)
# ==========================================

def recursive_planner(intent, depth=0, max_depth=4):
    indent = "    " * depth
    prefix = "└── " if depth > 0 else "[ROOT] "
    print(f"{indent}{prefix}處理意圖: {intent}")
    
    if depth >= max_depth:
        return

    result_json = call_llm_decompose(intent)
    if not result_json:
        return

    sub_intents = result_json.get("sub_intents", [])
    
    for sub in sub_intents:
        content = sub['content']
        is_atomic = sub.get('is_atomic', False)
        source = sub.get('atomic_source')
        # 取得時間標記
        sched_time = sub.get('scheduled_start', 'N/A')

        if is_atomic:
            marker = "🟢 [EXEC]" if source == "pre_defined" else "🔴 [NEW]"
            # 在輸出中標示確定時間
            print(f"{indent}    [{sched_time}] {marker} {content} (Type: {source})")
        else:
            time.sleep(0.1)
            print(f"{indent}    >>> Scheduled for: {sched_time}")
            recursive_planner(content, depth + 1, max_depth)

# ==========================================
# 4. 執行
# ==========================================

if __name__ == "__main__":
    root_intent = "準備 301 會議室，下午兩點要跟客戶進行視訊提案。"
    
    print("=== GIAS 意圖拆解系統啟動 (Time-Aware Mode) ===")
    print("-" * 50)
    recursive_planner(root_intent, max_depth=4)

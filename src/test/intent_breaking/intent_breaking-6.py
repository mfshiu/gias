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
    "Switch_Conditioner(Conditioner_ID, State)": "Control conditioner state.",
    "Play_Music(Music_Style)": "Play music.",
    "Turn_Light(Light_ID, State)": "Control light state.",
    "Say(Text)": "Speech output for interaction."
}

# ==========================================
# 2. 核心 Prompt 設計 (維持原樣)
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
# 3. 遞迴邏輯 (修正：回傳完整 JSON 結構)
# ==========================================

def recursive_planner(intent, depth=0, max_depth=4, scheduled_start="N/A", node_id="root"):
    """
    遞迴拆解意圖，並回傳完整的計畫樹狀結構 (Dictionary)。
    """
    indent = "    " * depth
    prefix = "└── " if depth > 0 else "[ROOT] "
    
    # 初始化當前節點結構
    current_node = {
        "id": node_id,
        "intent": intent,
        "depth": depth,
        "scheduled_start": scheduled_start,
        "type": "composite",  # 預設為複合意圖，除非被判定為 Atomic
        "sub_plans": [],      # 存放子節點的遞迴結果
        "execution_logic": [] # 存放本層級的執行順序 (Relationships)
    }

    # === 強制終止條件：到達最大深度 ===
    if depth >= max_depth:
        print(f"{indent}[{scheduled_start}] 🔴 [NEW] {intent} (Type: leaf_forced_atomic)")
        current_node["type"] = "leaf_forced_atomic"
        current_node["is_atomic"] = True
        current_node["atomic_source"] = "new_generated"
        return current_node

    print(f"{indent}{prefix}處理意圖: {intent}")

    result_json = call_llm_decompose(intent)
    
    # 若 LLM 呼叫失敗，回傳當前狀態作為 Error Node
    if not result_json:
        current_node["error"] = "decomposition_failed"
        return current_node

    # 填入本層級的執行邏輯 (Sequence/Parallel)
    current_node["execution_logic"] = result_json.get("relationships", [])
    
    sub_intents = result_json.get("sub_intents", [])
    
    # 若無子意圖，標記為 Leaf (雖然理論上 LLM 應該在 is_atomic 處理，但防呆)
    if not sub_intents:
        current_node["type"] = "leaf_no_children"
        current_node["is_atomic"] = True
        return current_node

    # 處理每一個子意圖
    for sub in sub_intents:
        sub_id = sub.get('id', 'unknown') # LLM 產生的臨時 ID，用於 mapping relationship
        content = sub['content']
        is_atomic = sub.get('is_atomic', False)
        source = sub.get('atomic_source')
        sched_time = sub.get('scheduled_start', 'N/A')
        
        # 用於遞迴的 ID (加上 depth 避免重複，或直接用 LLM 給的)
        unique_sub_id = f"{depth+1}_{sub_id}"

        if is_atomic:
            # === 原子意圖：不再遞迴，直接建立葉節點 ===
            marker = "🟢 [EXEC]" if source == "pre_defined" else "🔴 [NEW]"
            print(f"{indent}    [{sched_time}] {marker} {content} (Type: {source})")
            
            atomic_node = {
                "id": sub_id, # 保留 LLM 原始 ID 以對應 execution_logic
                "intent": content,
                "depth": depth + 1,
                "scheduled_start": sched_time,
                "type": "atomic",
                "is_atomic": True,
                "atomic_source": source,
                "sub_plans": [] # 原子意圖無子計畫
            }
            current_node["sub_plans"].append(atomic_node)
            
        else:
            # === 複合意圖：遞迴呼叫，並將結果掛載到 sub_plans ===
            time.sleep(0.1) # Rate limit protection
            
            # 遞迴取得子樹
            child_plan_tree = recursive_planner(
                intent=content, 
                depth=depth + 1, 
                max_depth=max_depth, 
                scheduled_start=sched_time,
                node_id=sub_id # 傳遞 ID 以維持結構一致性
            )
            
            # 確保子樹正確回傳後加入
            if child_plan_tree:
                current_node["sub_plans"].append(child_plan_tree)

    return current_node

# ==========================================
# 4. 執行與驗證
# ==========================================

if __name__ == "__main__":
    # 測試案例
    root_intent = "下午三點移動至 301 會議室，開啟空調。"
    root_intent = "一邊播放輕音樂，一邊把燈光調暗。"
    
    print("=== GIAS 意圖拆解系統啟動 (JSON Return Mode) ===")
    print("-" * 50)
    
    # 執行規劃並取得完整 JSON 物件
    full_plan = recursive_planner(root_intent, max_depth=4)
    
    print("-" * 50)
    print("=== 最終生成的執行計畫 (JSON) ===")
    
    # 將結果存檔或印出 (模擬傳給執行層)
    if full_plan:
        # 使用 ensure_ascii=False 確保中文正常顯示
        json_output = json.dumps(full_plan, indent=2, ensure_ascii=False)
        print(json_output)
        
        # 選擇性：存成檔案
        # with open("execution_plan.json", "w", encoding="utf-8") as f:
        #     f.write(json_output)

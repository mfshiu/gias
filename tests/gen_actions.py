# test/gen_actions/gen_actions.py
# GIAS Knowledge Graph Construction Script (No Auth Mode)
import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI

# ==========================================
# 1. 初始化設定 (修改為 No Auth)
# ==========================================
load_dotenv()

# 設定 URI (預設本地端)
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")

# --- 修改處：設定 AUTH 為 None ---
AUTH = None 

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 簡單防呆
if not OPENAI_API_KEY:
    print("⚠️ Warning: No API Key found. Please set OPENAI_API_KEY in .env")

client = OpenAI(api_key=OPENAI_API_KEY)

# 建立 Driver (auth=None)
driver = GraphDatabase.driver(URI, auth=AUTH)

def get_embedding(text):
    """呼叫 OpenAI 取得向量 (1536維)"""
    # 使用 text-embedding-3-small 以節省成本並保持高效
    response = client.embeddings.create(input=text, model="text-embedding-3-small")
    return response.data[0].embedding

# ==========================================
# 2. 定義資料 (10組工具 + Slot定義)
# ==========================================
slot_definitions = {
    "Location": "A physical place, room, city, or region.",
    "Time": "A temporal expression, including specific times, dates, or durations.",
    "Temperature": "A numeric value representing degrees of heat.",
    "Keyword": "A general search term, category, or name.",
    "Content": "Free text content, titles, or descriptions.",
    "Person": "A name of a human being or contact."
}

tools_data = [
    # --- IoT ---
    {"action": "iot_turn_on_light", "behavior": "Turn on the light in a specific area.", "slots": [{"name": "Location", "reason": "The specific room or area in the house to light up."}]},
    {"action": "iot_set_ac_temp", "behavior": "Set the air conditioner temperature.", "slots": [{"name": "Temperature", "reason": "The target temperature value in degrees Celsius."}]},
    {"action": "iot_open_curtains", "behavior": "Open the smart curtains or blinds.", "slots": [{"name": "Location", "reason": "The room where the curtains are located."}]},
    
    # --- System / Time ---
    {"action": "sys_set_alarm", "behavior": "Set an alarm for a specific time.", "slots": [{"name": "Time", "reason": "The specific point in time when the alarm should ring."}]},
    {"action": "sys_start_timer", "behavior": "Start a countdown timer.", "slots": [{"name": "Time", "reason": "The duration or length of time to count down."}]},
    {"action": "sys_add_calendar", "behavior": "Add a new event to the calendar.", "slots": [{"name": "Content", "reason": "The title or subject of the event."}, {"name": "Time", "reason": "The date and start time of the event."}]},

    # --- Info ---
    {"action": "info_query_weather", "behavior": "Check the weather forecast.", "slots": [{"name": "Location", "reason": "The city or region to check weather for."}]},
    {"action": "info_search_restaurant", "behavior": "Find restaurants or food nearby.", "slots": [{"name": "Keyword", "reason": "The type of food or specific restaurant name."}]},

    # --- Media / Comm ---
    {"action": "media_play_music", "behavior": "Play music tracks or songs.", "slots": [{"name": "Keyword", "reason": "The name of the artist, song title, or genre."}]},
    {"action": "comm_send_message", "behavior": "Send a text message to someone.", "slots": [{"name": "Person", "reason": "The name of the recipient contact."}, {"name": "Content", "reason": "The body text of the message."}]}
]

# ==========================================
# 3. 執行建庫 (Cypher Execution)
# ==========================================
def build_knowledge_graph(tx):
    print("🚀 Starting KG Construction (No Auth Mode)...")
    
    # --- Step A: 建立 Slot 節點 (含向量) ---
    print("   -> Creating Slots...")
    for name, desc in slot_definitions.items():
        slot_vec = get_embedding(desc)
        query = """
        MERGE (s:Slot {name: $name})
        SET s.desc = $desc, 
            s.vector = $vector
        """
        tx.run(query, name=name, desc=desc, vector=slot_vec)
        
    # --- Step B: 建立 Action 與 關係 (含向量) ---
    print("   -> Creating Actions and Relationships...")
    for tool in tools_data:
        action_name = tool["action"]
        behavior = tool["behavior"]
        
        # 1. 計算 Action 向量
        action_vec = get_embedding(behavior)
        
        # 2. 建立 Action 節點
        query_action = """
        MERGE (a:Action {name: $name})
        SET a.behavior = $behavior, 
            a.vector = $vector
        """
        tx.run(query_action, name=action_name, behavior=behavior, vector=action_vec)
        
        # 3. 建立 Relationships (含 Reason 向量)
        for slot in tool["slots"]:
            slot_name = slot["name"]
            reason_text = slot["reason"]
            
            # 計算 Reason 向量
            reason_vec = get_embedding(reason_text)
            
            query_rel = """
            MATCH (a:Action {name: $action_name})
            MATCH (s:Slot {name: $slot_name})
            MERGE (a)-[r:REQUIRES]->(s)
            SET r.reason = $reason,
                r.vector = $reason_vec
            """
            tx.run(query_rel, 
                   action_name=action_name, 
                   slot_name=slot_name, 
                   reason=reason_text, 
                   reason_vec=reason_vec)
            
            print(f"      Connected: {action_name} --[{reason_text[:20]}...]--> {slot_name}")

# 執行主程式
try:
    with driver.session() as session:
        session.execute_write(build_knowledge_graph)
    print("\n✅ Knowledge Graph Built Successfully with Vectors!")
except Exception as e:
    print(f"\n❌ Error: {e}")
    print("Tip: Please check if 'dbms.security.auth_enabled=false' is set in your neo4j.conf")
finally:
    driver.close()
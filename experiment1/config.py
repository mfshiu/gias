"""
Experiment 1 共用環境/常數設定。
"""

from __future__ import annotations

from src.core.intent.domain_profile import DomainProfile

# -----------------------------------------------------------------------------
# 圖譜環境（節點 = 展區/POI/攤位；邊 = CONNECTED_TO 含 distance）
# 為了讓導航有足夠的距離變化與替代路徑，比 seed_blackboard 更豐富。
# -----------------------------------------------------------------------------

ZONES = [
    "Main_Hall",
    "AI_Tech_Area",
    "Robotics_Area",
    "Gaming_Area",
    "VR_Area",
    "Exit_Hall",
]

POIS = [
    {"id": "P_Entrance", "name": "Main Entrance", "zone": "Main_Hall"},
    {"id": "P_Info", "name": "Information Desk", "zone": "Main_Hall"},
    {"id": "P_Restroom_S", "name": "Restroom_South", "zone": "Gaming_Area"},
    {"id": "P_Restroom_N", "name": "Restroom_North", "zone": "AI_Tech_Area"},
    {"id": "P_Cafe", "name": "Cafe", "zone": "VR_Area"},
    {"id": "P_Exit", "name": "Main Exit", "zone": "Exit_Hall"},
]

BOOTHS = [
    {"id": "B_AI1", "exhibitor": "TechCorp AI", "zone": "AI_Tech_Area"},
    {"id": "B_AI2", "exhibitor": "DeepMind Lab", "zone": "AI_Tech_Area"},
    {"id": "B_RB1", "exhibitor": "Robotics Inc", "zone": "Robotics_Area"},
    {"id": "B_RB2", "exhibitor": "AutoBot Co", "zone": "Robotics_Area"},
    {"id": "B_GM1", "exhibitor": "GameStudio X", "zone": "Gaming_Area"},
    {"id": "B_GM2", "exhibitor": "PixelArts", "zone": "Gaming_Area"},
    {"id": "B_VR1", "exhibitor": "VirtualReality+", "zone": "VR_Area"},
]

# (a_id, b_id, distance_m)
EDGES = [
    ("P_Entrance", "P_Info", 10),
    ("P_Info", "B_AI1", 25),
    ("P_Info", "B_RB1", 30),
    ("P_Info", "B_GM1", 35),
    ("B_AI1", "B_AI2", 15),
    ("B_AI2", "P_Restroom_N", 10),
    ("B_AI1", "B_RB1", 20),
    ("B_RB1", "B_RB2", 15),
    ("B_RB2", "B_VR1", 25),
    ("B_VR1", "P_Cafe", 10),
    ("B_GM1", "B_GM2", 15),
    ("B_GM2", "P_Restroom_S", 10),
    ("B_GM2", "B_VR1", 20),
    ("P_Cafe", "P_Exit", 20),
    ("P_Restroom_N", "P_Exit", 30),
]

# 預設 Zone 人潮狀態
DEFAULT_CROWD_STATE = "Normal"

# Walking speed (m/s) for completion-time simulation
WALK_SPEED_MPS = 1.0

# 重新規劃懲罰時間（秒）
REPLAN_OVERHEAD_SEC = 5.0

# 動態事件種類
EVENT_KIND_CROWD = "crowd_congestion"
EVENT_KIND_CLOSURE = "area_closure"
EVENT_KIND_DETOUR = "route_detour"

# 區域閉鎖狀態名稱（與 State.status_name 對應）
STATE_CROWDED = "Crowded"
STATE_CLOSED = "Closed"
STATE_NORMAL = "Normal"
STATE_SPARSE = "Sparse"

# -----------------------------------------------------------------------------
# Domain Profile（experiment1 專用，覆蓋 expo + 額外 zone 同義詞）
# -----------------------------------------------------------------------------

EXPERIMENT_PROFILE = DomainProfile(
    name="experiment1_expo",
    synonym_rules=[
        (r"廠商", "展商"),
        (r"有賣", "販售"),
        (r"賣", "販售"),
        (r"帶我去", "引導我前往"),
        (r"帶領我到", "引導我前往"),
        (r"領我到", "引導我前往"),
        (r"先去", "首先前往"),
        (r"再去", "接著前往"),
        (r"然後去", "接著前往"),
        (r"避開", "避免"),
        (r"繞過", "避免"),
        (r"避免人多的地方", "避免擁擠區"),
        (r"避免人潮", "避免擁擠區"),
    ],
    action_alias={
        "RecommendExhibits": ["推薦", "哪裡有", "有賣", "販售", "找", "展商", "攤位", "廠商"],
        "LocateExhibit": ["帶我去", "引導我前往", "前往", "去", "在哪", "位置", "首先前往", "接著前往"],
        "ExplainExhibit": ["介紹", "說明", "展品"],
        "ExplainDirections": ["怎麼走", "怎麼去", "路線", "方向"],
        "SuggestRoute": ["路線", "怎麼安排", "規劃", "安排路線", "建議路線", "避開", "避免", "避免擁擠區"],
        "CrowdStatus": ["人多", "擁擠", "人潮"],
        "LocateFacility": ["洗手間", "廁所", "服務台", "無障礙"],
        "AnswerFAQ": ["幾點", "開放", "閉館", "FAQ"],
        "ProvideSchedule": ["活動", "時間", "schedule"],
        "NavigationAssistance": ["導航", "帶路", "引導"],
    },
    slot_map={
        "target_name": ["destination", "target", "目標", "target_name", "終點", "區域", "展區"],
        "target_type": ["類型", "目標類型", "type"],
        "current_location": ["location", "目前位置", "起點", "current_location", "出發點", "入口"],
        "destination": ["target", "目標", "target_name", "終點"],
    },
    enum_alias={
        "target_type": {
            "攤位": "booth",
            "展區": "exhibit_zone",
            "展品": "exhibit",
            "區域": "exhibit_zone",
            "區": "exhibit_zone",
        },
        "facility_type": {
            "廁所": "restroom",
            "洗手間": "restroom",
            "出口": "exit",
            "服務台": "service_desk",
            "無障礙": "accessible",
        },
    },
)

# -----------------------------------------------------------------------------
# 自然語言 zone 名稱對應（給 test cases 用）
# -----------------------------------------------------------------------------

ZONE_LABELS = {
    "AI_Tech_Area": "AI 展區",
    "Robotics_Area": "機器人展區",
    "Gaming_Area": "遊戲展區",
    "VR_Area": "VR 展區",
    "Main_Hall": "主大廳",
    "Exit_Hall": "出口大廳",
}

# 提供導航 action 類別歸類，方便 metrics 判斷
NAV_ACTIONS = {
    "LocateExhibit",
    "SuggestRoute",
    "NavigationAssistance",
    "ExplainDirections",
}

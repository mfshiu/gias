"""
Navigation Scenario：地圖、Domain Profile、常數設定

本模組的展場地圖比 experiment1 更豐富（10 zones、多條替代路徑），
目的是讓 30% / 60% 注入的動態事件（壅塞 / 關閉 / 改道）
能觸發有意義的重規劃，並有足夠的距離變化來評估「路徑效率」。
"""

from __future__ import annotations

from src.core.intent.domain_profile import DomainProfile


# =============================================================================
# 展場地圖
# =============================================================================
#
# 拓撲示意（雙向邊；數字 = 公尺）：
#
#                  P_Restroom_N --15-- B_AI2 --15-- B_AI1
#                        |                            |
#                       30                           20
#                        |                            |
#                       AI_Tech_Area               P_North_Hub
#                                                     |
#   P_Bio1 --20-- Biotech_Area                       12
#       |              |                              |
#      18             25                              |
#       |              |                              |
#   P_Entrance --10-- P_Info --25-- B_RB1 --15-- B_RB2 --20-- B_IoT1
#       |              |               (Robotics)              (IoT)
#      30             20                                          |
#       |              |                                         15
#       |              |                                          |
#       |          B_SU1 (Startup)                           B_IoT2
#       |              |                                          |
#       |             20                                         20
#       |              |                                          |
#       +-------- P_South_Hub --18-- B_GM1 --15-- B_GM2 --20-- B_VR1
#                      |              (Gaming)                  (VR)
#                     22                                          |
#                      |                                         10
#                      |                                          |
#                  P_Restroom_S                                 P_Cafe
#                                                                 |
#                                                                20
#                                                                 |
#                                                              P_Exit (Exit_Hall)
#
# 這個拓撲有兩條主幹（北：AI/Bio/IoT/VR；南：Startup/Gaming/VR），
# VR_Area 同時被北南兩條路徑連通，所以單一邊或單一節點封閉幾乎
# 一定可以找到替代路徑（適合測試 replan）。

ZONES: list[str] = [
    "Main_Hall",
    "AI_Tech_Area",
    "Robotics_Area",
    "Gaming_Area",
    "VR_Area",
    "IoT_Area",
    "Biotech_Area",
    "Startup_Area",
    "Food_Court",
    "Exit_Hall",
]

POIS: list[dict] = [
    {"id": "P_Entrance",   "name": "Main Entrance",     "zone": "Main_Hall"},
    {"id": "P_Info",       "name": "Information Desk",  "zone": "Main_Hall"},
    {"id": "P_North_Hub",  "name": "North Hub",         "zone": "Main_Hall"},
    {"id": "P_South_Hub",  "name": "South Hub",         "zone": "Main_Hall"},
    {"id": "P_Restroom_N", "name": "Restroom North",    "zone": "AI_Tech_Area"},
    {"id": "P_Restroom_S", "name": "Restroom South",    "zone": "Gaming_Area"},
    {"id": "P_Cafe",       "name": "Cafe",              "zone": "Food_Court"},
    {"id": "P_Bio1",       "name": "Biotech Lounge",    "zone": "Biotech_Area"},
    {"id": "P_Exit",       "name": "Main Exit",         "zone": "Exit_Hall"},
]

BOOTHS: list[dict] = [
    {"id": "B_AI1",   "exhibitor": "TechCorp AI",     "zone": "AI_Tech_Area"},
    {"id": "B_AI2",   "exhibitor": "DeepMind Lab",    "zone": "AI_Tech_Area"},
    {"id": "B_RB1",   "exhibitor": "Robotics Inc",    "zone": "Robotics_Area"},
    {"id": "B_RB2",   "exhibitor": "AutoBot Co",      "zone": "Robotics_Area"},
    {"id": "B_GM1",   "exhibitor": "GameStudio X",    "zone": "Gaming_Area"},
    {"id": "B_GM2",   "exhibitor": "PixelArts",       "zone": "Gaming_Area"},
    {"id": "B_VR1",   "exhibitor": "VirtualReality+", "zone": "VR_Area"},
    {"id": "B_IoT1",  "exhibitor": "IoT Solutions",   "zone": "IoT_Area"},
    {"id": "B_IoT2",  "exhibitor": "SmartHome Co",    "zone": "IoT_Area"},
    {"id": "B_BIO1",  "exhibitor": "BioGen Labs",     "zone": "Biotech_Area"},
    {"id": "B_SU1",   "exhibitor": "Startup Showcase","zone": "Startup_Area"},
]

# (a_id, b_id, distance_m)；建立為雙向 CONNECTED_TO 邊
EDGES: list[tuple[str, str, int]] = [
    # Entrance / Info / Hubs
    ("P_Entrance",   "P_Info",        10),
    ("P_Entrance",   "P_Bio1",        30),
    ("P_Info",       "P_North_Hub",   12),
    ("P_Info",       "P_South_Hub",   20),
    ("P_Info",       "B_RB1",         25),
    ("P_Info",       "B_SU1",         20),
    # AI / Biotech 北側
    ("P_North_Hub",  "B_AI1",         20),
    ("B_AI1",        "B_AI2",         15),
    ("B_AI2",        "P_Restroom_N",  15),
    ("P_Bio1",       "B_BIO1",        18),
    ("B_BIO1",       "B_AI1",         22),
    # Robotics / IoT 走廊
    ("B_RB1",        "B_RB2",         15),
    ("B_RB2",        "B_IoT1",        20),
    ("B_IoT1",       "B_IoT2",        15),
    ("B_IoT2",       "B_VR1",         20),
    # Startup / Gaming / VR 南側
    ("B_SU1",        "P_South_Hub",   20),
    ("P_South_Hub",  "B_GM1",         18),
    ("P_South_Hub",  "P_Restroom_S",  22),
    ("B_GM1",        "B_GM2",         15),
    ("B_GM2",        "B_VR1",         20),
    # VR → Cafe → Exit
    ("B_VR1",        "P_Cafe",        10),
    ("P_Cafe",       "P_Exit",        20),
    ("P_Restroom_N", "P_Exit",        35),
    # 額外冗餘邊（提供替代路徑，讓 replan 有更多選擇）
    ("B_RB1",        "B_AI1",         18),
    ("B_GM2",        "B_IoT2",        25),
]


# =============================================================================
# 動態事件與狀態常數
# =============================================================================

DEFAULT_CROWD_STATE = "Normal"
WALK_SPEED_MPS = 1.0        # 模擬完成時間用的步行速度
REPLAN_OVERHEAD_SEC = 5.0   # 重新規劃懲罰時間（秒）

# 注入比例：任務進行到 30% 或 60% 時觸發動態事件
INJECTION_RATIOS: tuple[float, ...] = (0.3, 0.6)

# 動態事件種類
EVENT_KIND_CROWD = "crowd_congestion"
EVENT_KIND_CLOSURE = "area_closure"
EVENT_KIND_DETOUR = "route_detour"

ALL_EVENT_KINDS: tuple[str, ...] = (
    EVENT_KIND_CROWD,
    EVENT_KIND_CLOSURE,
    EVENT_KIND_DETOUR,
)

# Zone 狀態名（與 State.status_name 對應）
STATE_NORMAL = "Normal"
STATE_CROWDED = "Crowded"
STATE_CLOSED = "Closed"
STATE_SPARSE = "Sparse"


# =============================================================================
# Domain Profile（Navigation 專用）
# =============================================================================

NAVIGATION_PROFILE = DomainProfile(
    name="navigation_scenario_expo",
    synonym_rules=[
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
        (r"重新規劃", "重新規劃路線"),
        (r"改道", "重新規劃路線"),
    ],
    action_alias={
        "LocateExhibit": [
            "帶我去", "引導我前往", "前往", "去", "在哪", "位置",
            "首先前往", "接著前往",
        ],
        "ExplainDirections": ["怎麼走", "怎麼去", "路線", "方向"],
        "SuggestRoute": [
            "規劃", "安排路線", "建議路線", "避開", "避免",
            "避免擁擠區", "最佳路線",
        ],
        "NavigationAssistance": ["導航", "帶路", "引導", "走"],
        "CrowdStatus": ["人多", "擁擠", "人潮"],
        "LocateFacility": ["洗手間", "廁所", "服務台", "無障礙", "出口"],
        "ReplanRoute": ["重新規劃路線", "改道", "繞", "封閉", "改走"],
    },
    slot_map={
        "target_name": [
            "destination", "target", "目標", "target_name",
            "終點", "區域", "展區",
        ],
        "target_type": ["類型", "目標類型", "type"],
        "current_location": [
            "location", "目前位置", "起點", "current_location",
            "出發點", "入口",
        ],
        "destination": ["target", "目標", "target_name", "終點"],
    },
    enum_alias={
        "target_type": {
            "攤位": "booth",
            "展區": "exhibit_zone",
            "區域": "exhibit_zone",
            "區": "exhibit_zone",
            "展品": "exhibit",
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


# =============================================================================
# 自然語言對應（給 test cases 用）
# =============================================================================

ZONE_LABELS: dict[str, str] = {
    "Main_Hall":      "主大廳",
    "AI_Tech_Area":   "AI 展區",
    "Robotics_Area":  "機器人展區",
    "Gaming_Area":    "遊戲展區",
    "VR_Area":        "VR 展區",
    "IoT_Area":       "IoT 展區",
    "Biotech_Area":   "生技展區",
    "Startup_Area":   "新創展區",
    "Food_Court":     "美食區",
    "Exit_Hall":      "出口大廳",
}


# 導航類 action：metrics / plan 檢查時用
NAV_ACTIONS: set[str] = {
    "LocateExhibit",
    "SuggestRoute",
    "NavigationAssistance",
    "ExplainDirections",
    "ReplanRoute",
}

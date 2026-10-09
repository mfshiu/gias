"""
GenExam Scenario：領域知識（KG-RAG 來源）、約束與動態事件常數

本模組定義「試題生成」評估會用到的環境永續領域知識：
  - 6 個 TOPIC（主題）
  - 20+ 個 SUBTOPIC（子主題）
  - 70+ 個 CONCEPT（概念，KG-RAG 檢索粒度）
  - 100+ 個 FACT（事實陳述，題目命題的素材）
  - 10+ 個 SOURCE（來源，供 Knowledge Grounding 驗證）

另外定義：
  - DIFFICULTY / BLOOM / QUESTION_TYPE 三大約束維度
  - 動態事件（注入時機 & 種類）常數
  - Bot / State / Skill 列表
"""

from __future__ import annotations


# =============================================================================
# 約束維度
# =============================================================================
DIFFICULTY_LEVELS: tuple[str, ...] = ("easy", "medium", "hard")

BLOOM_LEVELS: list[dict] = [
    {"level": 1, "name": "Remember"},
    {"level": 2, "name": "Understand"},
    {"level": 3, "name": "Apply"},
    {"level": 4, "name": "Analyze"},
    {"level": 5, "name": "Evaluate"},
    {"level": 6, "name": "Create"},
]

QUESTION_TYPES: tuple[str, ...] = (
    "mcq",            # 選擇題
    "true_false",     # 是非題
    "short_answer",   # 簡答題
    "cloze",          # 填空題
)


# =============================================================================
# Difficulty / Bloom Rubric（給 generator / refiner / judge 內化標準）
# =============================================================================
# 設計目的：解決「LLM 自證 hard 但 judge 判 medium」的問題。
# 把「什麼算 hard / 什麼算 bloom=4」明確化，三方（generator/refiner/judge）共用。
DIFFICULTY_RUBRIC: dict[str, str] = {
    "easy": (
        "easy：直接回憶單一概念或事實；題幹只描述單一情境；干擾選項與正解明顯不同；"
        "不需推理或跨概念整合；國中／高一可作答。"
    ),
    "medium": (
        "medium：需理解單一概念並做一步應用或對比；干擾選項是該主題相關概念但屬於"
        "不同子主題；需 1 步推理；高二／高三可作答。"
    ),
    "hard": (
        "hard：需跨 ≥ 2 個 Concept 的因果鏈或多步推理（例：A 導致 B，B 影響 C，因此…）；"
        "題幹需含情境資料（數值、限制、條件）；干擾選項中至少 2 個是「概念相近但細節不同」"
        "的陷阱（例：機制相似但對象不同／因果方向相反／時間尺度不同）；"
        "需鑑別性思考、進階／大學程度。"
        "判斷準則：若題目能僅憑單一 Concept 或單一 Fact 直接得解，即非 hard。"
    ),
}

BLOOM_RUBRIC: dict[int, str] = {
    1: ("Remember：辨識／回憶事實、名詞、定義。常見動詞：列出、定義、識別。"),
    2: ("Understand：用自己的話解釋概念、舉例、分類。常見動詞：解釋、比較、舉例。"),
    3: ("Apply：把學到的概念套用到新情境執行步驟。常見動詞：計算、套用、解決。"),
    4: ("Analyze：拆解問題、辨識成分間的關係、找出因果或矛盾。"
        "常見動詞：分析、區辨、推論。題目需提供情境資料讓考生「拆解」。"),
    5: ("Evaluate：依據準則對方案做價值判斷、權衡。常見動詞：評估、批判、辯護。"
        "題目要求學生選出「最適」「最有效」並能合理說明理由。"),
    6: ("Create：整合多項知識產生新方案或假設。常見動詞：設計、規劃、提出。"),
}


# =============================================================================
# 來源（Source）
# =============================================================================
SOURCES: list[dict] = [
    {"id": "SRC_IPCC_AR6", "title": "IPCC AR6 Synthesis Report",
     "year": 2023, "url": "https://www.ipcc.ch/report/ar6/syr/",
     "credibility": 0.98},
    {"id": "SRC_WHO_AQG", "title": "WHO Global Air Quality Guidelines",
     "year": 2021, "url": "https://www.who.int/publications/i/item/9789240034228",
     "credibility": 0.97},
    {"id": "SRC_US_EPA", "title": "US EPA Environmental Topics",
     "year": 2024, "url": "https://www.epa.gov/environmental-topics",
     "credibility": 0.95},
    {"id": "SRC_TW_MOENV", "title": "中華民國環境部資訊",
     "year": 2025, "url": "https://www.moenv.gov.tw/",
     "credibility": 0.93},
    {"id": "SRC_UN_SDG", "title": "United Nations Sustainable Development Goals",
     "year": 2024, "url": "https://sdgs.un.org/goals",
     "credibility": 0.95},
    {"id": "SRC_IUCN_RL", "title": "IUCN Red List of Threatened Species",
     "year": 2024, "url": "https://www.iucnredlist.org/",
     "credibility": 0.96},
    {"id": "SRC_IEA_WEO", "title": "IEA World Energy Outlook",
     "year": 2024, "url": "https://www.iea.org/reports/world-energy-outlook-2024",
     "credibility": 0.95},
    {"id": "SRC_ELLEN_CE", "title": "Ellen MacArthur Foundation – Circular Economy",
     "year": 2023, "url": "https://ellenmacarthurfoundation.org/",
     "credibility": 0.92},
    {"id": "SRC_WB_ENV", "title": "World Bank Environment Data",
     "year": 2024, "url": "https://data.worldbank.org/topic/environment",
     "credibility": 0.93},
    {"id": "SRC_WWF_LP", "title": "WWF Living Planet Report",
     "year": 2024, "url": "https://www.worldwildlife.org/",
     "credibility": 0.91},
]


# =============================================================================
# Topic / Subtopic / Concept / Fact 主資料
# =============================================================================
#
# 結構說明：
#   TOPICS: list[dict]
#     - id, name, name_zh
#     - subtopics: list[Subtopic]
#         - id, name, name_zh
#         - concepts: list[Concept]
#             - id, name, name_zh, definition
#             - facts: list[Fact]
#                 - id, statement, source_id
#
# 設計原則：
#   1. 每個 Topic 給 3–4 個 Subtopic
#   2. 每個 Subtopic 給 3–5 個 Concept
#   3. 每個 Concept 至少 1 條 Fact，重點概念 2 條（合計 ~120 條）
#
# 命名規則：
#   T_<topic>           e.g. T_AIR_POLLUTION
#   ST_<topic>_<sub>    e.g. ST_AIR_PM
#   C_<concept_id>      e.g. C_PM25
#   F_<concept>_<n>     e.g. F_PM25_1

TOPICS: list[dict] = [
    # -------------------------------------------------------------------------
    # 1. AIR POLLUTION
    # -------------------------------------------------------------------------
    {
        "id": "T_AIR_POLLUTION",
        "name": "air_pollution",
        "name_zh": "空氣污染",
        "subtopics": [
            {
                "id": "ST_AIR_PM",
                "name": "particulate_matter",
                "name_zh": "懸浮微粒",
                "concepts": [
                    {
                        "id": "C_PM25", "name": "PM2.5", "name_zh": "細懸浮微粒 PM2.5",
                        "definition": "空氣動力學直徑 ≤ 2.5 微米的懸浮微粒，可穿透肺泡進入血液循環。",
                        "facts": [
                            {"id": "F_PM25_1",
                             "statement": "WHO 2021 指引將年均 PM2.5 安全值由 10 µg/m³ 下修至 5 µg/m³。",
                             "source_id": "SRC_WHO_AQG"},
                            {"id": "F_PM25_2",
                             "statement": "長期暴露於 PM2.5 與肺癌、心血管疾病及早死亡率顯著相關。",
                             "source_id": "SRC_WHO_AQG"},
                        ],
                    },
                    {
                        "id": "C_PM10", "name": "PM10", "name_zh": "粗懸浮微粒 PM10",
                        "definition": "空氣動力學直徑 ≤ 10 微米的懸浮微粒，主要沉積於上呼吸道。",
                        "facts": [
                            {"id": "F_PM10_1",
                             "statement": "WHO 建議 PM10 年均濃度上限為 15 µg/m³。",
                             "source_id": "SRC_WHO_AQG"},
                        ],
                    },
                    {
                        "id": "C_SECONDARY_AEROSOL", "name": "secondary_aerosol",
                        "name_zh": "二次氣膠",
                        "definition": "由 SO₂、NOx、VOC 等前驅物在大氣中經化學反應生成的微粒。",
                        "facts": [
                            {"id": "F_SECAERO_1",
                             "statement": "二次氣膠是都會區 PM2.5 的主要組成之一，常佔 30–50%。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                    {
                        "id": "C_AQI", "name": "AQI",
                        "name_zh": "空氣品質指標 AQI",
                        "definition": "綜合 PM2.5、PM10、O₃、SO₂、NO₂、CO 換算的單一指標。",
                        "facts": [
                            {"id": "F_AQI_1",
                             "statement": "AQI 介於 0–50 為良好；151–200 對所有族群皆為不健康。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_AIR_OZONE",
                "name": "ozone_smog",
                "name_zh": "臭氧與光化煙霧",
                "concepts": [
                    {
                        "id": "C_GROUND_O3", "name": "ground_level_ozone",
                        "name_zh": "地面臭氧",
                        "definition": "由 NOx 與 VOC 在陽光下反應生成的二次污染物。",
                        "facts": [
                            {"id": "F_GO3_1",
                             "statement": "地面臭氧是夏季光化煙霧的主要成分，會引發呼吸道發炎。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                    {
                        "id": "C_NOX_VOC", "name": "nox_voc_reaction",
                        "name_zh": "NOx-VOC 光化反應",
                        "definition": "NOx 與 VOC 在紫外線下產生臭氧與其他光化氧化物的反應路徑。",
                        "facts": [
                            {"id": "F_NV_1",
                             "statement": "VOC/NOx 比值決定臭氧生成的限制因子（VOC-limited 或 NOx-limited）。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                    {
                        "id": "C_STRATO_O3", "name": "stratospheric_ozone",
                        "name_zh": "平流層臭氧",
                        "definition": "存在於平流層的臭氧層，可吸收 UV-B，與地面臭氧危害無關。",
                        "facts": [
                            {"id": "F_SO3_1",
                             "statement": "蒙特婁議定書管制 CFC 後，南極臭氧洞自 2000 年起逐漸縮小。",
                             "source_id": "SRC_IPCC_AR6"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_AIR_INDOOR",
                "name": "indoor_air_quality",
                "name_zh": "室內空氣品質",
                "concepts": [
                    {
                        "id": "C_FORMALDEHYDE", "name": "formaldehyde",
                        "name_zh": "甲醛",
                        "definition": "由建材黏著劑釋出的揮發性有機物，IARC 列為 Group 1 致癌物。",
                        "facts": [
                            {"id": "F_HCHO_1",
                             "statement": "台灣室內空氣品質標準將甲醛 8 小時平均值定為 0.08 ppm。",
                             "source_id": "SRC_TW_MOENV"},
                        ],
                    },
                    {
                        "id": "C_INDOOR_CO2", "name": "indoor_co2",
                        "name_zh": "室內 CO₂",
                        "definition": "代表通風品質的指標，過高會造成疲倦與注意力下降。",
                        "facts": [
                            {"id": "F_ICO2_1",
                             "statement": "室內 CO₂ 1,000 ppm 以下通常代表通風充足。",
                             "source_id": "SRC_TW_MOENV"},
                        ],
                    },
                    {
                        "id": "C_RADON", "name": "radon",
                        "name_zh": "氡氣",
                        "definition": "天然放射性氣體，是非吸菸者肺癌的第二大成因。",
                        "facts": [
                            {"id": "F_RADON_1",
                             "statement": "EPA 建議室內氡濃度高於 4 pCi/L 即需採取緩減措施。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_AIR_MOBILE",
                "name": "mobile_emissions",
                "name_zh": "移動污染源",
                "concepts": [
                    {
                        "id": "C_DIESEL_PM", "name": "diesel_particulate",
                        "name_zh": "柴油微粒",
                        "definition": "柴油引擎排出的黑碳微粒，含 PAH 等致癌物。",
                        "facts": [
                            {"id": "F_DPM_1",
                             "statement": "柴油排氣已被 IARC 確認為 Group 1 致癌物（2012）。",
                             "source_id": "SRC_WHO_AQG"},
                        ],
                    },
                    {
                        "id": "C_CATALYTIC", "name": "catalytic_converter",
                        "name_zh": "三元觸媒轉換器",
                        "definition": "汽油車排氣處理元件，同時還原 NOx 並氧化 CO 與 HC。",
                        "facts": [
                            {"id": "F_CAT_1",
                             "statement": "三元觸媒需在引擎溫度達 250°C 以上才能達到工作效率。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                ],
            },
        ],
    },

    # -------------------------------------------------------------------------
    # 2. WASTE MANAGEMENT
    # -------------------------------------------------------------------------
    {
        "id": "T_WASTE_MANAGEMENT",
        "name": "waste_management",
        "name_zh": "廢棄物管理",
        "subtopics": [
            {
                "id": "ST_WASTE_CLASSIFY",
                "name": "waste_classification",
                "name_zh": "廢棄物分類",
                "concepts": [
                    {
                        "id": "C_RECYCLABLES", "name": "recyclables",
                        "name_zh": "資源回收物",
                        "definition": "可經分類處理後再製為原料或產品的廢棄物。",
                        "facts": [
                            {"id": "F_REC_1",
                             "statement": "紙、塑膠、金屬、玻璃為四大主要資源回收類別。",
                             "source_id": "SRC_TW_MOENV"},
                        ],
                    },
                    {
                        "id": "C_ORGANIC_WASTE", "name": "organic_waste",
                        "name_zh": "有機廢棄物",
                        "definition": "可被微生物分解的廚餘與庭園廢棄物。",
                        "facts": [
                            {"id": "F_ORG_1",
                             "statement": "廚餘堆肥可降低掩埋場 30% 以上的甲烷排放量。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                    {
                        "id": "C_PLASTIC_TYPES", "name": "plastic_types_1_to_7",
                        "name_zh": "塑膠分類 1–7",
                        "definition": "依樹脂代碼分類的 7 類塑膠，回收性能差異甚大。",
                        "facts": [
                            {"id": "F_PT_1",
                             "statement": "PET (#1) 與 HDPE (#2) 為最廣泛被回收的塑膠類別。",
                             "source_id": "SRC_ELLEN_CE"},
                        ],
                    },
                    {
                        "id": "C_E_WASTE", "name": "e_waste",
                        "name_zh": "電子廢棄物",
                        "definition": "廢電器、電子產品與零組件的統稱。",
                        "facts": [
                            {"id": "F_EW_1",
                             "statement": "全球每年產生超過 5,000 萬公噸電子廢棄物，回收率不到 20%。",
                             "source_id": "SRC_UN_SDG"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_WASTE_CE",
                "name": "circular_economy",
                "name_zh": "循環經濟",
                "concepts": [
                    {
                        "id": "C_CE", "name": "circular_economy",
                        "name_zh": "循環經濟",
                        "definition": "以再利用、再製造、再循環取代「製造-使用-丟棄」線性模式。",
                        "facts": [
                            {"id": "F_CE_1",
                             "statement": "Ellen MacArthur 提出 ReSOLVE 框架推動產品全生命週期價值最大化。",
                             "source_id": "SRC_ELLEN_CE"},
                        ],
                    },
                    {
                        "id": "C_3R", "name": "3r_principle",
                        "name_zh": "3R 原則",
                        "definition": "Reduce / Reuse / Recycle 為廢棄物管理優先順序。",
                        "facts": [
                            {"id": "F_3R_1",
                             "statement": "3R 中以「Reduce」減量效益最大，「Recycle」次之。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                    {
                        "id": "C_EPR", "name": "extended_producer_responsibility",
                        "name_zh": "延伸生產者責任 EPR",
                        "definition": "生產者需負責產品使用後的回收與處理成本。",
                        "facts": [
                            {"id": "F_EPR_1",
                             "statement": "歐盟 WEEE 指令是電子廢棄物 EPR 的代表性立法。",
                             "source_id": "SRC_ELLEN_CE"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_WASTE_HAZARD",
                "name": "hazardous_waste",
                "name_zh": "有害廢棄物",
                "concepts": [
                    {
                        "id": "C_BATTERY_REC", "name": "battery_recycling",
                        "name_zh": "電池回收",
                        "definition": "鎳鎘、鋰電池等含重金屬，需專案回收避免污染。",
                        "facts": [
                            {"id": "F_BAT_1",
                             "statement": "鋰電池熱失控可釋出氟化氫並引發火災，需獨立貯存。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                    {
                        "id": "C_MEDICAL_WASTE", "name": "medical_waste",
                        "name_zh": "醫療廢棄物",
                        "definition": "源自醫療院所的感染性、針具或藥物廢棄物。",
                        "facts": [
                            {"id": "F_MED_1",
                             "statement": "感染性醫療廢棄物原則上以高溫滅菌或焚化處理。",
                             "source_id": "SRC_TW_MOENV"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_WASTE_WTE",
                "name": "waste_to_energy",
                "name_zh": "廢棄物能源化",
                "concepts": [
                    {
                        "id": "C_INCINERATION", "name": "incineration",
                        "name_zh": "焚化",
                        "definition": "高溫燃燒廢棄物產生熱能與蒸氣發電。",
                        "facts": [
                            {"id": "F_INC_1",
                             "statement": "現代焚化爐配備濕式洗滌與活性碳處理，戴奧辛排放可低於 0.1 ng-TEQ/Nm³。",
                             "source_id": "SRC_TW_MOENV"},
                        ],
                    },
                    {
                        "id": "C_AD", "name": "anaerobic_digestion",
                        "name_zh": "厭氧消化",
                        "definition": "在無氧條件下分解有機廢棄物產生沼氣。",
                        "facts": [
                            {"id": "F_AD_1",
                             "statement": "沼氣主成分為 50–70% 甲烷，可用於發電或加熱。",
                             "source_id": "SRC_IEA_WEO"},
                        ],
                    },
                ],
            },
        ],
    },

    # -------------------------------------------------------------------------
    # 3. CLIMATE CHANGE
    # -------------------------------------------------------------------------
    {
        "id": "T_CLIMATE_CHANGE",
        "name": "climate_change",
        "name_zh": "氣候變遷",
        "subtopics": [
            {
                "id": "ST_CC_GHG",
                "name": "greenhouse_gases",
                "name_zh": "溫室氣體",
                "concepts": [
                    {
                        "id": "C_CO2", "name": "co2",
                        "name_zh": "二氧化碳 CO₂",
                        "definition": "燃燒化石燃料的主要溫室氣體，工業革命前濃度 ~280 ppm。",
                        "facts": [
                            {"id": "F_CO2_1",
                             "statement": "2023 年全球年均 CO₂ 濃度突破 420 ppm，為近 200 萬年最高。",
                             "source_id": "SRC_IPCC_AR6"},
                        ],
                    },
                    {
                        "id": "C_CH4", "name": "methane",
                        "name_zh": "甲烷 CH₄",
                        "definition": "100 年 GWP 約為 CO₂ 的 28 倍的短壽命強效溫室氣體。",
                        "facts": [
                            {"id": "F_CH4_1",
                             "statement": "甲烷大氣壽命約 12 年，主要來源為畜牧、油氣與廢棄物。",
                             "source_id": "SRC_IPCC_AR6"},
                        ],
                    },
                    {
                        "id": "C_N2O", "name": "n2o",
                        "name_zh": "一氧化二氮 N₂O",
                        "definition": "100 年 GWP 約 273 倍，主要來自農業氮肥。",
                        "facts": [
                            {"id": "F_N2O_1",
                             "statement": "N₂O 大氣壽命超過 100 年，會破壞平流層臭氧。",
                             "source_id": "SRC_IPCC_AR6"},
                        ],
                    },
                    {
                        "id": "C_GWP", "name": "gwp",
                        "name_zh": "全球暖化潛勢 GWP",
                        "definition": "以 CO₂ 為基準（GWP=1）的相對溫室效應強度。",
                        "facts": [
                            {"id": "F_GWP_1",
                             "statement": "SF₆ 的 GWP100 超過 23,000，是已知最強的人為溫室氣體之一。",
                             "source_id": "SRC_IPCC_AR6"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_CC_MITIGATION",
                "name": "mitigation_strategies",
                "name_zh": "減緩策略",
                "concepts": [
                    {
                        "id": "C_NET_ZERO", "name": "net_zero",
                        "name_zh": "淨零排放",
                        "definition": "人為溫室氣體排放與移除量達成平衡的狀態。",
                        "facts": [
                            {"id": "F_NZ_1",
                             "statement": "巴黎協定目標將升溫控制在 1.5°C 內，需 2050 年達成全球淨零。",
                             "source_id": "SRC_IPCC_AR6"},
                        ],
                    },
                    {
                        "id": "C_CARBON_CREDIT", "name": "carbon_credit",
                        "name_zh": "碳權",
                        "definition": "1 公噸 CO₂e 減排或移除可交易的權利憑證。",
                        "facts": [
                            {"id": "F_CC_1",
                             "statement": "碳權須通過 ISO 14064 或 VCS 等第三方查證才具公信力。",
                             "source_id": "SRC_UN_SDG"},
                        ],
                    },
                    {
                        "id": "C_CCS", "name": "ccs",
                        "name_zh": "碳捕捉與封存 CCS",
                        "definition": "從工業排放源捕捉 CO₂ 並注入地層永久封存。",
                        "facts": [
                            {"id": "F_CCS_1",
                             "statement": "IPCC 認定 CCS 為達成 1.5°C 路徑的必要技術之一。",
                             "source_id": "SRC_IPCC_AR6"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_CC_ADAPT",
                "name": "adaptation_strategies",
                "name_zh": "調適策略",
                "concepts": [
                    {
                        "id": "C_SEA_LEVEL", "name": "sea_level_rise",
                        "name_zh": "海平面上升",
                        "definition": "因海水熱膨脹與冰川融化導致的全球海平面升高。",
                        "facts": [
                            {"id": "F_SLR_1",
                             "statement": "IPCC AR6 預估到 2100 年全球海平面可能上升 0.5–1.0 公尺。",
                             "source_id": "SRC_IPCC_AR6"},
                        ],
                    },
                    {
                        "id": "C_CLIMATE_RESILIENCE", "name": "climate_resilience",
                        "name_zh": "氣候韌性",
                        "definition": "系統承受、適應並從氣候衝擊回復的能力。",
                        "facts": [
                            {"id": "F_CR_1",
                             "statement": "氣候韌性常透過「保護-接納-撤離」三層次規劃推動。",
                             "source_id": "SRC_WB_ENV"},
                        ],
                    },
                ],
            },
        ],
    },

    # -------------------------------------------------------------------------
    # 4. WATER RESOURCES
    # -------------------------------------------------------------------------
    {
        "id": "T_WATER_RESOURCES",
        "name": "water_resources",
        "name_zh": "水資源",
        "subtopics": [
            {
                "id": "ST_WATER_POLLUTION",
                "name": "water_pollution",
                "name_zh": "水污染",
                "concepts": [
                    {
                        "id": "C_EUTROPHICATION", "name": "eutrophication",
                        "name_zh": "優養化",
                        "definition": "水體氮磷過量導致藻類大量繁殖、溶氧下降的現象。",
                        "facts": [
                            {"id": "F_EUT_1",
                             "statement": "農業逕流與生活污水是優養化最主要的營養鹽來源。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                    {
                        "id": "C_BOD_COD", "name": "bod_cod",
                        "name_zh": "BOD 與 COD",
                        "definition": "生化需氧量 (BOD) 與化學需氧量 (COD) 衡量水體有機污染。",
                        "facts": [
                            {"id": "F_BC_1",
                             "statement": "BOD5 高於 5 mg/L 通常代表水體受到輕度有機污染。",
                             "source_id": "SRC_TW_MOENV"},
                        ],
                    },
                    {
                        "id": "C_HEAVY_METAL", "name": "heavy_metal_contamination",
                        "name_zh": "重金屬污染",
                        "definition": "Hg、Cd、Pb 等金屬於水體中累積，產生生物放大效應。",
                        "facts": [
                            {"id": "F_HM_1",
                             "statement": "水俁病是有機汞污染海產引發的中樞神經疾病典型案例。",
                             "source_id": "SRC_WHO_AQG"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_WATER_CONSERVE",
                "name": "water_conservation",
                "name_zh": "節水",
                "concepts": [
                    {
                        "id": "C_RAINWATER", "name": "rainwater_harvesting",
                        "name_zh": "雨水回收",
                        "definition": "收集屋頂或地面雨水經過濾後再利用。",
                        "facts": [
                            {"id": "F_RW_1",
                             "statement": "綠建築標章鼓勵雨水回收用於澆灌與沖廁。",
                             "source_id": "SRC_TW_MOENV"},
                        ],
                    },
                    {
                        "id": "C_GRAY_WATER", "name": "gray_water_reuse",
                        "name_zh": "中水回收",
                        "definition": "將洗手、淋浴等低污染廢水經處理再用於非飲用用途。",
                        "facts": [
                            {"id": "F_GW_1",
                             "statement": "中水回收可降低建築用水量 30–50%。",
                             "source_id": "SRC_UN_SDG"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_WATER_TREATMENT",
                "name": "wastewater_treatment",
                "name_zh": "污水處理",
                "concepts": [
                    {
                        "id": "C_TREAT_PRIMARY", "name": "primary_treatment",
                        "name_zh": "初級處理",
                        "definition": "以沉澱、篩濾去除大顆粒與懸浮固體。",
                        "facts": [
                            {"id": "F_TP_1",
                             "statement": "初級處理通常可去除 50–70% 懸浮固體。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                    {
                        "id": "C_TREAT_SECONDARY", "name": "secondary_treatment",
                        "name_zh": "二級處理",
                        "definition": "利用活性污泥或生物膜降解有機污染物。",
                        "facts": [
                            {"id": "F_TS_1",
                             "statement": "二級處理可降低 BOD 與懸浮固體 80–95%。",
                             "source_id": "SRC_US_EPA"},
                        ],
                    },
                    {
                        "id": "C_SLUDGE", "name": "sludge_handling",
                        "name_zh": "污泥處理",
                        "definition": "處理後產生的污泥需濃縮、消化、脫水與最終處置。",
                        "facts": [
                            {"id": "F_SL_1",
                             "statement": "厭氧消化可同時穩定污泥並回收沼氣。",
                             "source_id": "SRC_IEA_WEO"},
                        ],
                    },
                ],
            },
        ],
    },

    # -------------------------------------------------------------------------
    # 5. BIODIVERSITY
    # -------------------------------------------------------------------------
    {
        "id": "T_BIODIVERSITY",
        "name": "biodiversity",
        "name_zh": "生物多樣性",
        "subtopics": [
            {
                "id": "ST_BIO_EXTINCT",
                "name": "species_extinction",
                "name_zh": "物種滅絕",
                "concepts": [
                    {
                        "id": "C_IUCN", "name": "iucn_red_list",
                        "name_zh": "IUCN 紅皮書",
                        "definition": "全球公認的物種瀕危等級評估系統。",
                        "facts": [
                            {"id": "F_IUCN_1",
                             "statement": "IUCN 紅皮書分為 LC, NT, VU, EN, CR, EW, EX 等等級。",
                             "source_id": "SRC_IUCN_RL"},
                        ],
                    },
                    {
                        "id": "C_INVASIVE", "name": "invasive_species",
                        "name_zh": "外來入侵種",
                        "definition": "非原生且對本地生態造成負面影響的物種。",
                        "facts": [
                            {"id": "F_INV_1",
                             "statement": "外來入侵種是全球生物多樣性喪失的第二大威脅。",
                             "source_id": "SRC_WWF_LP"},
                        ],
                    },
                    {
                        "id": "C_DEFAUNATION", "name": "defaunation",
                        "name_zh": "動物相消失",
                        "definition": "野生動物族群在區域內顯著減少或滅絕。",
                        "facts": [
                            {"id": "F_DEF_1",
                             "statement": "WWF 估算 1970–2020 年全球野生脊椎動物族群平均下降約 69%。",
                             "source_id": "SRC_WWF_LP"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_BIO_SERVICE",
                "name": "ecosystem_services",
                "name_zh": "生態系服務",
                "concepts": [
                    {
                        "id": "C_POLLINATION", "name": "pollination_service",
                        "name_zh": "授粉服務",
                        "definition": "蜂類、鳥類等動物協助植物授粉的生態系功能。",
                        "facts": [
                            {"id": "F_POL_1",
                             "statement": "全球約 75% 的糧食作物依賴動物授粉。",
                             "source_id": "SRC_UN_SDG"},
                        ],
                    },
                    {
                        "id": "C_FOREST_C", "name": "forest_carbon_sequestration",
                        "name_zh": "森林碳匯",
                        "definition": "森林透過光合作用吸收並固定大氣 CO₂。",
                        "facts": [
                            {"id": "F_FC_1",
                             "statement": "熱帶雨林每公頃平均年吸收 0.5–2 公噸 CO₂。",
                             "source_id": "SRC_IPCC_AR6"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_BIO_CONSERVE",
                "name": "conservation",
                "name_zh": "保育",
                "concepts": [
                    {
                        "id": "C_PROTECTED_AREA", "name": "protected_areas",
                        "name_zh": "保護區",
                        "definition": "依法劃設以保育生物多樣性與文化資源的區域。",
                        "facts": [
                            {"id": "F_PA_1",
                             "statement": "昆明-蒙特婁全球生物多樣性框架目標 2030 年保護地表 30%。",
                             "source_id": "SRC_UN_SDG"},
                        ],
                    },
                    {
                        "id": "C_HABITAT_CORRIDOR", "name": "habitat_corridor",
                        "name_zh": "棲地廊道",
                        "definition": "連接被切割棲地的線狀生態通道，維持基因交流。",
                        "facts": [
                            {"id": "F_HC_1",
                             "statement": "野生動物天橋與生態箱涵屬於常見的棲地廊道設計。",
                             "source_id": "SRC_IUCN_RL"},
                        ],
                    },
                ],
            },
        ],
    },

    # -------------------------------------------------------------------------
    # 6. ENERGY CONSERVATION
    # -------------------------------------------------------------------------
    {
        "id": "T_ENERGY_CONSERVATION",
        "name": "energy_conservation",
        "name_zh": "能源節約",
        "subtopics": [
            {
                "id": "ST_EC_BUILDING",
                "name": "building_efficiency",
                "name_zh": "建築節能",
                "concepts": [
                    {
                        "id": "C_INSULATION_U", "name": "insulation_u_value",
                        "name_zh": "建築外殼 U 值",
                        "definition": "單位面積、單位溫差下穿透建築外殼的熱通量。",
                        "facts": [
                            {"id": "F_UV_1",
                             "statement": "U 值越低，外殼隔熱性能越好；綠建築鼓勵 U ≤ 2.0 W/m²·K。",
                             "source_id": "SRC_TW_MOENV"},
                        ],
                    },
                    {
                        "id": "C_LED", "name": "led_lighting",
                        "name_zh": "LED 照明",
                        "definition": "以發光二極體取代白熾燈與螢光燈的高效率照明。",
                        "facts": [
                            {"id": "F_LED_1",
                             "statement": "LED 燈具能源效率約為白熾燈泡的 5–8 倍。",
                             "source_id": "SRC_IEA_WEO"},
                        ],
                    },
                    {
                        "id": "C_ENERGY_LABEL", "name": "energy_star_label",
                        "name_zh": "能源效率標章",
                        "definition": "依產品實測能效標示等級，協助消費者選擇高效節能產品。",
                        "facts": [
                            {"id": "F_EL_1",
                             "statement": "台灣能源效率分級標章共分 5 級，1 級最節能。",
                             "source_id": "SRC_TW_MOENV"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_EC_TRANSPORT",
                "name": "transportation",
                "name_zh": "綠色運輸",
                "concepts": [
                    {
                        "id": "C_PUBLIC_TRANSIT", "name": "public_transit",
                        "name_zh": "大眾運輸",
                        "definition": "提供多人共乘的公共運輸系統，提升人均能源效率。",
                        "facts": [
                            {"id": "F_PUBTRANS_1",
                             "statement": "鐵路運輸每人每公里能耗約為私人小客車的 1/4 至 1/8。",
                             "source_id": "SRC_IEA_WEO"},
                        ],
                    },
                    {
                        "id": "C_EV", "name": "electric_vehicle",
                        "name_zh": "電動車",
                        "definition": "由電池驅動的零尾氣排放車輛。",
                        "facts": [
                            {"id": "F_EV_1",
                             "statement": "電動車生命週期碳排是否較低，取決於電網綠電比例。",
                             "source_id": "SRC_IEA_WEO"},
                        ],
                    },
                ],
            },
            {
                "id": "ST_EC_RENEWABLE",
                "name": "renewable_sources",
                "name_zh": "再生能源",
                "concepts": [
                    {
                        "id": "C_SOLAR_PV", "name": "solar_pv",
                        "name_zh": "太陽光電",
                        "definition": "以矽晶或薄膜半導體將太陽輻射直接轉換為電能。",
                        "facts": [
                            {"id": "F_PV_1",
                             "statement": "商業矽晶模組典型轉換效率為 18–22%。",
                             "source_id": "SRC_IEA_WEO"},
                        ],
                    },
                    {
                        "id": "C_WIND", "name": "wind_turbine",
                        "name_zh": "風力發電",
                        "definition": "利用風能驅動葉片帶動發電機。",
                        "facts": [
                            {"id": "F_WT_1",
                             "statement": "離岸風電容量因素通常為陸域風電的 1.5–2 倍。",
                             "source_id": "SRC_IEA_WEO"},
                        ],
                    },
                    {
                        "id": "C_GEOTHERMAL", "name": "geothermal",
                        "name_zh": "地熱",
                        "definition": "利用地殼熱能進行發電或供暖。",
                        "facts": [
                            {"id": "F_GEO_1",
                             "statement": "增強型地熱系統 (EGS) 可在非火山地區開發深層熱源。",
                             "source_id": "SRC_IEA_WEO"},
                        ],
                    },
                ],
            },
        ],
    },
]


# =============================================================================
# Agent / Skill / State 列表（GVR 閉環）
# =============================================================================
SKILLS: tuple[str, ...] = (
    "Question_Authoring",       # GeneratorBot
    "Question_Verification",    # VerifierBot
    "Question_Refinement",      # RefinerBot
    "Knowledge_Retrieval",      # 共用
    "Generation_Coordination",  # CoordinatorBot
)

AGENTS: list[dict] = [
    {"agent_id": "CoordinatorBot_01", "type": "Coordinator",
     "skills": ("Generation_Coordination", "Knowledge_Retrieval")},
    {"agent_id": "GeneratorBot_01", "type": "Generator",
     "skills": ("Question_Authoring", "Knowledge_Retrieval")},
    {"agent_id": "VerifierBot_01", "type": "Verifier",
     "skills": ("Question_Verification", "Knowledge_Retrieval")},
    {"agent_id": "RefinerBot_01", "type": "Refiner",
     "skills": ("Question_Refinement", "Knowledge_Retrieval")},
]

AGENT_STATES: tuple[str, ...] = (
    "Idle",
    "Planning",
    "Retrieving",
    "Generating",
    "Verifying",
    "Refining",
    "Finalized",
)


# =============================================================================
# 動態事件 & 注入時機
# =============================================================================
INJECTION_PHASE_INITIAL = "initial"
INJECTION_PHASE_MID = "mid"
ALL_INJECTION_PHASES: tuple[str, ...] = (
    INJECTION_PHASE_INITIAL,
    INJECTION_PHASE_MID,
)

EVENT_ADD_CONSTRAINT = "add_constraint"            # 新增一個原本沒有的約束
EVENT_TIGHTEN_CONSTRAINT = "tighten_constraint"    # 收緊既有約束 (e.g., medium → hard)
EVENT_SWAP_CONSTRAINT = "swap_constraint"          # 替換約束 (e.g., mcq → true_false)
EVENT_RELAX_CONSTRAINT = "relax_constraint"        # 鬆綁約束 (e.g., hard → medium)

ALL_EVENT_KINDS: tuple[str, ...] = (
    EVENT_ADD_CONSTRAINT,
    EVENT_TIGHTEN_CONSTRAINT,
    EVENT_SWAP_CONSTRAINT,
    EVENT_RELAX_CONSTRAINT,
)


# =============================================================================
# 統計與雜項
# =============================================================================
def total_counts() -> dict[str, int]:
    """方便 seed_blackboard 印 baseline 規模摘要。"""
    n_topics = len(TOPICS)
    n_subs = 0
    n_concepts = 0
    n_facts = 0
    for t in TOPICS:
        n_subs += len(t["subtopics"])
        for s in t["subtopics"]:
            n_concepts += len(s["concepts"])
            for c in s["concepts"]:
                n_facts += len(c["facts"])
    return {
        "topics": n_topics,
        "subtopics": n_subs,
        "concepts": n_concepts,
        "facts": n_facts,
        "sources": len(SOURCES),
        "bloom_levels": len(BLOOM_LEVELS),
        "difficulties": len(DIFFICULTY_LEVELS),
        "question_types": len(QUESTION_TYPES),
        "agents": len(AGENTS),
        "skills": len(SKILLS),
        "states": len(AGENT_STATES),
    }

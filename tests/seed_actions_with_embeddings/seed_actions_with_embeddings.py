# tests/seed_actions_with_embeddings.py
"""
Seed the Neo4j knowledge graph with Action nodes having real LLM embeddings.

This script:
1) Clears existing (:Action) nodes
2) Generates real embeddings via LLMClient.embed_text()
3) Inserts 10 predefined Action nodes into Neo4j
4) Ensures a vector index for description_embedding
"""

import os
from dotenv import load_dotenv

from src.app_helper import get_agent_config
from src.llm.client import LLMClient
from src.kg.adapter_neo4j import Neo4jBoltAdapter


# -------------------------
# 測試用 Action 定義（10 組）
# -------------------------
ACTIONS = [
    ("LocateExhibit", "引導訪客前往指定展區或攤位位置"),
    ("ExplainExhibit", "介紹指定展品或展區的內容與特色"),
    ("SuggestRoute", "根據訪客位置與需求規劃最佳參觀路線"),
    ("AnswerFAQ", "回答關於會場規則、開放時間與服務設施的常見問題"),
    ("LocateFacility", "協助查找洗手間、出口、服務台或無障礙設施"),
    ("ProvideSchedule", "提供活動、演講或表演的時間與地點資訊"),
    ("RecommendExhibits", "根據訪客興趣推薦適合的展區或活動"),
    ("CrowdStatus", "說明各展區目前的人潮與擁擠狀況"),
    ("NavigationAssistance", "在移動過程中即時提供方向與轉彎提示"),
    ("ExplainDirections", "以自然語言解釋如何從目前位置前往目的地"),
]


def main():
    # ✅ 不再 load_dotenv()；也不再讀 OPENAI_API_KEY / OPENAI_KEY
    cfg = get_agent_config()

    # --- KG adapter（仍依 gias.toml / agent_config）---
    kg_cfg = cfg.get("kg", {})
    if kg_cfg.get("type") != "neo4j":
        raise RuntimeError("KG type must be neo4j for this seed script")

    neo4j_cfg = kg_cfg.get("neo4j")
    if not isinstance(neo4j_cfg, dict):
        raise RuntimeError("Missing [kg.neo4j] config in gias.toml")

    kg = Neo4jBoltAdapter.from_config(
        neo4j_cfg,
        logger=None,
    )

    # --- LLM client（✅ 完全使用 gias.toml / agent_config）---
    llm_cfg = cfg.get("llm")
    if not isinstance(llm_cfg, dict):
        raise RuntimeError("Missing [llm] config in gias.toml")

    llm = LLMClient.from_config(cfg)

    print(">>> Clearing existing Action nodes")
    kg.write("MATCH (a:Action) DETACH DELETE a")

    print(">>> Generating embeddings and inserting Actions")

    dim: int | None = None

    for name, desc in ACTIONS:
        # ✅ 直接使用 LLMClient.embed_text() 取得真實 embedding
        emb = llm.embed_text(desc)

        if not isinstance(emb, list) or not emb:
            raise RuntimeError(f"Invalid embedding returned for action '{name}'")

        # 確保維度一致
        if dim is None:
            dim = len(emb)
        elif len(emb) != dim:
            raise RuntimeError(
                f"Embedding dimension mismatch: expected {dim}, got {len(emb)} for action '{name}'"
            )

        kg.write(
            """
            CREATE (a:Action {
              name: $name,
              description: $desc,
              description_embedding: $emb
            })
            """,
            {
                "name": name,
                "desc": desc,
                "emb": emb,
            },
        )
        print(f"  - Created Action: {name} (dim={len(emb)})")

    if dim is None:
        raise RuntimeError("No actions seeded; embedding dimension is unknown.")

    print(">>> Ensuring vector index (action_desc_vec)")
    kg.ensure_vector_index(
        index_name="action_desc_vec",
        label="Action",
        embedding_prop="description_embedding",
        dimensions=dim,
        similarity="cosine",
    )

    print(">>> Done. Seeded 10 Actions with real LLM embeddings.")
    kg.close()


if __name__ == "__main__":
    main()

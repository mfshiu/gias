# src/kg/action_store.py
from __future__ import annotations
from typing import List, Dict



class ActionStore:
    def __init__(self, kg_adapter):
        self.kg = kg_adapter


    def ensure_action_desc_index(self, *, dimensions: int):
        self.kg.ensure_vector_index(
            index_name="action_desc_vec",
            label="Action",
            embedding_prop="description_embedding",
            dimensions=dimensions,
            similarity="cosine",
        )


    def search_actions_by_vector(self, *, vector: List[float], top_k: int, min_score: float) -> List[Dict]:
        return self.kg.vector_query_nodes(
            index_name="action_desc_vec",
            vector=vector,
            top_k=top_k,
            min_score=min_score,
            return_props=["name", "description"],
        )

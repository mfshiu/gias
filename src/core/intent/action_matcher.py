from core.actions.models import ActionDef, ActionMatch
from kg.action_store import ActionStore
from .domain_profile import DomainProfile
from .embedder import LLMEmbedder

class ActionMatcher:
    def __init__(self, *, action_store: ActionStore, embedder: LLMEmbedder, domain: DomainProfile, logger):
        self.action_store = action_store
        self.embedder = embedder
        self.domain = domain
        self.logger = logger

    def _alias_score(self, action_name: str, normalized_text: str) -> float:
        aliases = self.domain.action_alias.get(action_name, [])
        hits = 0
        for trig in aliases:
            trig = (trig or "").strip()
            if trig and trig in normalized_text:
                hits += 1
        return min(1.0, hits * 0.25)

    def match_actions(
        self,
        intention: str,
        *,
        top_k: int = 10,
        min_score: float = 0.75,
        allow_fallback: bool = True,
        alias_weight: float = 0.15,
    ) -> list[ActionMatch]:
        norm_intent = self.domain.normalize(intention)
        self.logger.debug(f"Matching actions for sub-intention: {norm_intent}")

        q_vec = self.embedder.embed_text(norm_intent)
        dim = len(q_vec)

        self.action_store.ensure_action_desc_index(dimensions=dim)

        rows = self.action_store.search_actions_by_vector(
            vector=q_vec,
            top_k=top_k,
            min_score=min_score,
        )

        if (not rows) and allow_fallback:
            rows = self.action_store.search_actions_by_vector(
                vector=q_vec,
                top_k=top_k,
                min_score=0.0,
            )

        matches: list[ActionMatch] = []
        for r in rows or []:
            action_name = r.get("name") or "UnnamedAction"
            vec_score = float(r.get("score", 0.0))
            alias_score = self._alias_score(action_name, norm_intent)
            final_score = (1.0 - alias_weight) * vec_score + alias_weight * alias_score

            action_def = ActionDef(
                name=action_name,
                description=r.get("description") or "",
                meta={
                    "action_id": r.get("id"),
                    "kg_node": r.get("kg_node"),
                    "source": r.get("source"),
                },
            )

            matches.append(ActionMatch(
                action=action_def,
                score=float(final_score),
                evidence={
                    "matched_text": intention,
                    "normalized_intent": norm_intent,
                    "vector_score": vec_score,
                    "alias_score": alias_score,
                    "alias_weight": alias_weight,
                },
            ))

        matches.sort(key=lambda m: m.score, reverse=True)
        self.logger.info(f"Matched actions: {len(matches)}")
        return matches

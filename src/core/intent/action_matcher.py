# src/core/intent/action_matcher.py
from __future__ import annotations

from typing import Any, Mapping

from core.actions.models import ActionDef, ActionMatch
from kg.action_store import ActionStore
from .domain_profile import DomainProfile
from .embedder import LLMEmbedder


class ActionMatcher:
    """
    ActionMatcher = 向量檢索(action desc) + alias 加權 + (可選) 參數可填性/相似度重排

    ✅ 可落地重點：
    - 若呼叫端提供 slots（SubIntent.slots），就會：
      1) 從 KG 取 action 的 params schema（Action->Param）
      2) 做 required 可填性 gate（填不出就淘汰）
      3) 計算 param_score
      4) 依 final_score 重新排序
      5) 若 param_score / final_score 低於門檻，可直接淘汰（更能拒絕做不到的意圖）
    - 若 slots 未提供：維持舊行為（只做向量+alias）
    """

    def __init__(self, *, action_store: ActionStore, embedder: LLMEmbedder, domain: DomainProfile, logger):
        self.action_store = action_store
        self.embedder = embedder
        self.domain = domain
        self.logger = logger

    # -------------------------
    # Alias scoring (existing)
    # -------------------------
    def _alias_score(self, action_name: str, normalized_text: str) -> float:
        aliases = getattr(self.domain, "action_alias", {}).get(action_name, []) or []
        hits = 0
        for trig in aliases:
            trig = (trig or "").strip()
            if trig and trig in normalized_text:
                hits += 1
        return min(1.0, hits * 0.25)

    # -------------------------
    # Param scoring (new)
    # -------------------------
    def _get_slot_value(self, slots: Mapping[str, Any], param_key: str) -> Any | None:
        """slot → param 的最小可落地對應：同名 + DomainProfile.slot_map (若存在)"""
        if not slots:
            return None

        if param_key in slots:
            return slots[param_key]

        slot_map = getattr(self.domain, "slot_map", {}) or {}
        for alt in slot_map.get(param_key, []) or []:
            if alt in slots:
                return slots[alt]

        return None

    def _normalize_enum_value(self, v: Any) -> str:
        return self.domain.normalize(str(v)).strip()

    def _map_enum_alias(self, param_key: str, norm_v: str) -> str:
        """
        enum alias：例如 "攤位" -> "booth"
        建議你在 DomainProfile 增加：
          enum_alias = {
            "target_type": {"攤位": "booth", "展區": "exhibit_zone", "展品": "exhibit"},
            "facility_type": {"廁所":"restroom", ...}
          }
        若沒有 enum_alias，則原樣回傳。
        """
        enum_alias = getattr(self.domain, "enum_alias", {}) or {}
        mapped = (enum_alias.get(param_key, {}) or {}).get(norm_v)
        return mapped if mapped is not None else norm_v

    def _score_params(
        self,
        *,
        slots: Mapping[str, Any],
        params: list[dict[str, Any]],
    ) -> tuple[bool, dict[str, Any], float, list[dict[str, Any]]]:
        """
        回傳：
          fillable: required 是否皆可填
          bindings: param_key -> value
          param_score: 0~1
          param_evidence: 每個 param 的比對細節（debug 用）
        """
        bindings: dict[str, Any] = {}
        param_evidence: list[dict[str, Any]] = []

        # (1) required 可填性 gate
        for p in params or []:
            if bool(p.get("required")):
                key = p.get("key")
                if not key or not isinstance(key, str):
                    continue
                v = self._get_slot_value(slots, key)
                if v is None or (isinstance(v, str) and not v.strip()):
                    param_evidence.append(
                        {
                            "param": key,
                            "required": True,
                            "filled": False,
                            "reason": "required_missing",
                        }
                    )
                    return False, {}, 0.0, param_evidence

        # (2) 計分：required 權重更高
        total = 0.0
        total_w = 0.0

        for p in params or []:
            key = p.get("key")
            if not key or not isinstance(key, str):
                continue
            ptype = (p.get("type") or "").strip().lower()
            required = bool(p.get("required"))
            w = 2.0 if required else 1.0

            v = self._get_slot_value(slots, key)
            if v is None or (isinstance(v, str) and not v.strip()):
                # optional 沒填：不扣分，但也不加分
                param_evidence.append(
                    {
                        "param": key,
                        "type": ptype,
                        "required": required,
                        "filled": False,
                        "score": 0.0,
                        "reason": "optional_missing",
                    }
                )
                continue

            score = 0.0
            reason = ""

            if ptype == "enum":
                allowed = list(p.get("enum") or [])
                allowed_set = set(allowed)

                norm_v = self._normalize_enum_value(v)
                mapped_v = self._map_enum_alias(key, norm_v)

                if mapped_v in allowed_set:
                    score = 1.0
                    reason = "enum_match"
                else:
                    # enum 不在允許集合：可視為硬性不相容（score=0）
                    score = 0.0
                    reason = "enum_mismatch"

                bindings[key] = mapped_v if mapped_v in allowed_set else v

            elif ptype == "string":
                # 最小可落地：有填就給分；若你希望更嚴格可加入 pattern/ID 檢查
                s = str(v).strip()
                score = 0.8 if s else 0.0
                reason = "string_present" if s else "string_empty"
                bindings[key] = v

            elif ptype in ("int", "integer", "number", "float"):
                try:
                    float(v)
                    score = 0.7
                    reason = "number_parse_ok"
                    bindings[key] = v
                except Exception:
                    score = 0.0
                    reason = "number_parse_fail"
                    bindings[key] = v

            else:
                # 未知型別：保守給一點點分數（你也可改成 0）
                score = 0.5
                reason = f"unknown_type:{ptype}"
                bindings[key] = v

            total += w * score
            total_w += w

            param_evidence.append(
                {
                    "param": key,
                    "type": ptype,
                    "required": required,
                    "filled": True,
                    "value": v,
                    "score": score,
                    "reason": reason,
                }
            )

        param_score = (total / total_w) if total_w > 0 else 0.0
        return True, bindings, float(param_score), param_evidence

    # -------------------------
    # Main API
    # -------------------------
    def match_actions(
        self,
        intention: str,
        *,
        slots: Mapping[str, Any] | None = None,
        top_k: int = 10,
        min_score: float = 0.75,
        allow_fallback: bool = True,
        alias_weight: float = 0.15,
        w_base: float = 0.4,
        w_param: float = 0.6,
        min_param_score: float = 0.35,
        min_final_score: float = 0.55,
        enable_param_gate: bool = True,
    ) -> list[ActionMatch]:
        norm_intent = self.domain.normalize(intention)
        self.logger.debug(f"Matching actions for sub-intention: {norm_intent}")

        # ✅ 只保留「有效 slots」：忽略底線開頭（metadata / trace）
        raw_slots: Mapping[str, Any] = slots or {}
        effective_slots: dict[str, Any] = {
            str(k): v
            for k, v in raw_slots.items()
            if k is not None and not str(k).startswith("_")
        }
        use_slots = bool(effective_slots)

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
            a_score = self._alias_score(action_name, norm_intent)

            base_score = (1.0 - alias_weight) * vec_score + alias_weight * a_score

            action_def = ActionDef(
                name=action_name,
                description=r.get("description") or "",
                meta={
                    "action_id": r.get("id"),
                    "kg_node": r.get("kg_node"),
                    "source": r.get("source"),
                },
            )

            # ---- param scoring (optional) ----
            param_score = 0.0
            fillable = True
            bindings: dict[str, Any] = {}
            param_ev: list[dict[str, Any]] = []
            params_schema: list[dict[str, Any]] = []

            if use_slots:
                try:
                    params_schema = self.action_store.get_action_params(action_name) or []
                except Exception as e:
                    params_schema = []
                    self.logger.debug(f"get_action_params failed for {action_name}: {e}")

                if params_schema:
                    # ✅ 用有效 slots 計分/填參數
                    fillable, bindings, param_score, param_ev = self._score_params(
                        slots=effective_slots, params=params_schema
                    )
                else:
                    # ✅ schema 不可用：不 gate（避免全滅）
                    param_ev = [{"reason": "params_schema_unavailable"}]

            final_score = (w_base * base_score) + (w_param * param_score)

            # ✅ gate 只在「有效 slots 存在」且「schema 存在」時啟用
            reject_reason = ""
            should_gate = bool(use_slots and enable_param_gate and params_schema)

            if should_gate:
                if not fillable:
                    reject_reason = "required_params_missing"
                elif param_score < min_param_score:
                    reject_reason = "param_score_below_threshold"
                elif final_score < min_final_score:
                    reject_reason = "final_score_below_threshold"

                if reject_reason:
                    continue

            matches.append(
                ActionMatch.from_base(
                    action=action_def,
                    base_score=base_score,
                    param_score=param_score,
                    fillable=fillable,
                    bindings=bindings,
                    w_base=w_base,
                    w_param=w_param,
                    thresholds={"param": min_param_score, "final": min_final_score},
                    reject_reason=reject_reason,
                    evidence={
                        "matched_text": intention,
                        "normalized_intent": norm_intent,
                        "vector_score": vec_score,
                        "alias_score": a_score,
                        "alias_weight": alias_weight,
                        "base_score": base_score,
                        "param_score": param_score,
                        "final_score": final_score,
                        "slots_used": use_slots,
                        "effective_slots_keys": list(effective_slots.keys()),
                        "param_evidence": param_ev,
                        "weights": {"w_base": w_base, "w_param": w_param},
                        "thresholds": {"min_param_score": min_param_score, "min_final_score": min_final_score},
                        "param_gate_enabled": bool(should_gate),
                        "params_schema_available": bool(params_schema),
                    },
                )
            )

        matches.sort(key=lambda m: m.final_score, reverse=True)
        self.logger.info(f"Matched actions: {len(matches)}")
        return matches

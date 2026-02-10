from dataclasses import dataclass



@dataclass(frozen=True, slots=True)
class ActionDef:
    name: str
    description: str
    meta: dict | None = None   # e.g., kg_id, tool_name, preconditions...



@dataclass(frozen=True, slots=True)
class ActionMatch:
    action: ActionDef
    score: float
    evidence: dict | None = None

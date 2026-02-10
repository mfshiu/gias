from dataclasses import dataclass, field
from typing import Any

@dataclass
class SubIntent:
    intent: str
    slots: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] | None = None

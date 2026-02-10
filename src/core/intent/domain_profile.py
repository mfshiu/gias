import re
from dataclasses import dataclass, field

@dataclass
class DomainProfile:
    """
    通用、可插拔的領域設定：
    - synonym_rules: regex rewrite 做輕量語彙正規化
    - action_alias: action_name -> trigger strings，提供 alias boost
    """
    name: str = "generic"
    synonym_rules: list[tuple[str, str]] = field(default_factory=list)
    action_alias: dict[str, list[str]] = field(default_factory=dict)

    def normalize(self, text: str) -> str:
        t = (text or "").strip()
        t = re.sub(r"\s+", " ", t)

        for pat, repl in self.synonym_rules:
            try:
                t = re.sub(pat, repl, t, flags=re.IGNORECASE)
            except re.error:
                continue

        return t

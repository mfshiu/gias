"""
EntityMatcher：判斷計畫步驟的文字（intent、task、參數）是否提到某個實體。

取代原本的子字串比對（"A1" in "前往 A12" 為 True，會觸發不必要的重規劃）：
- 邊界：名稱以英數字開頭 / 結尾時，前 / 後一個字元不可是英數字或底線
  （"A1" 不會命中 "A12"、"B_A1" 不會命中 "B_A12"；中文緊鄰時仍可命中，如「前往A1攤位」）
- 大小寫不敏感；名稱中的空白可對應零或多個空白（"AI 展區" 命中 "AI展區"）
- 別名：實體 id 之外，也比對其他稱呼（DomainProfile.entity_aliases）
- 從屬：上層實體（如展區）的事件也會命中提到其下層實體（如區內攤位）的步驟
  （DomainProfile.entity_parents）；反向不成立

純資料運算，無 IO。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from functools import lru_cache

_WORD = "A-Za-z0-9_"


@lru_cache(maxsize=1024)
def _pattern(name: str) -> re.Pattern[str]:
    body = r"\s*".join(re.escape(part) for part in name.split())
    prefix = f"(?<![{_WORD}])" if re.match(f"[{_WORD}]", name) else ""
    suffix = f"(?![{_WORD}])" if re.search(f"[{_WORD}]$", name) else ""
    return re.compile(prefix + body + suffix, re.IGNORECASE)


def mentions_name(text: str, name: str) -> bool:
    """text 是否以完整名稱的形式提到 name（見模組說明的邊界規則）。"""
    name = (name or "").strip()
    return bool(name) and bool(_pattern(name).search(text or ""))


class EntityMatcher:
    def __init__(
        self,
        aliases: Mapping[str, Iterable[str]] | None = None,
        parents: Mapping[str, str] | None = None,
    ):
        self._aliases = {str(k): [str(a) for a in v if str(a).strip()] for k, v in (aliases or {}).items()}
        self._children: dict[str, list[str]] = {}
        for child, parent in (parents or {}).items():
            self._children.setdefault(str(parent), []).append(str(child))

    def names_for(self, entity: str) -> list[str]:
        """entity 本身、其別名，以及所有下層實體與它們的別名。"""
        names: list[str] = []
        seen: set[str] = set()
        stack = [entity]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            names.append(current)
            names.extend(self._aliases.get(current, []))
            stack.extend(self._children.get(current, []))
        return names

    def mentions(self, text: str, entity: str) -> bool:
        return any(mentions_name(text, name) for name in self.names_for(entity))

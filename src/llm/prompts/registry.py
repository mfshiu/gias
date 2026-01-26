# src/llm/prompts/registry.py
#
# PromptRegistry：載入/版本/模板渲染
# - 管理 templates/ 內的 prompt 檔案（例如 intent_parse_v1.md）
# - 支援簡單變數替換：{{var}}
# - 產出 OpenAI-style messages: [{"role":"system","content":"..."}, ...]
# - 內建 prompt_version 解析（從檔名推：*_vN.md）
#
# 使用方式（範例）：
#   registry = PromptRegistry.from_default()
#   messages, meta = registry.render(
#       "intent_parse_v1",
#       user_text="我要幫環境部做題庫",
#       ground_truth="(KG查到的資料...)",
#   )
#
# 建議 templates 內容格式（可選）：
#   ---system
#   你是...
#   ---user
#   使用者輸入：{{user_text}}
#   ---assistant
#   (可省略；通常不放)
#
# 若沒有 role 分段，則整份檔案視為 system prompt，
# 並由 render() 另外加入 user message。

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


Message = Dict[str, Any]


@dataclass(frozen=True)
class PromptMeta:
    name: str                 # e.g. "intent_parse_v1"
    version: Optional[str]    # e.g. "v1"
    path: str                 # absolute path
    roles: Tuple[str, ...]    # parsed roles in template


class PromptNotFoundError(FileNotFoundError):
    pass


class PromptTemplateError(RuntimeError):
    pass


class PromptRegistry:
    """
    以檔案系統做 Prompt Registry。
    預設目錄：<project_root>/src/llm/prompts/templates
    """

    ROLE_MARKERS = ("system", "user", "assistant", "tool")

    def __init__(self, template_dir: str):
        self.template_dir = Path(template_dir).resolve()
        if not self.template_dir.exists():
            raise PromptTemplateError(f"Prompt template directory not found: {self.template_dir}")

    @classmethod
    def from_default(cls) -> "PromptRegistry":
        """
        以本檔案位置推算 templates 位置：
        src/llm/prompts/registry.py -> src/llm/prompts/templates
        """
        here = Path(__file__).resolve()
        template_dir = here.parent / "templates"
        return cls(str(template_dir))

    def list_templates(self) -> List[str]:
        """
        列出所有模板（不含副檔名）：
        intent_parse_v1.md -> intent_parse_v1
        """
        results: List[str] = []
        for p in sorted(self.template_dir.glob("*.md")):
            results.append(p.stem)
        return results

    def resolve_path(self, name: str) -> Path:
        """
        name 可帶或不帶 .md
        """
        if name.endswith(".md"):
            p = (self.template_dir / name).resolve()
        else:
            p = (self.template_dir / f"{name}.md").resolve()

        if not p.exists():
            raise PromptNotFoundError(f"Prompt template not found: {p}")
        return p

    def load(self, name: str) -> Tuple[str, PromptMeta]:
        """
        讀取模板原文
        """
        p = self.resolve_path(name)
        text = p.read_text(encoding="utf-8")

        version = self._infer_version(p.stem)
        roles = tuple(self._peek_roles(text))
        meta = PromptMeta(name=p.stem, version=version, path=str(p), roles=roles)
        return text, meta

    def render(
        self,
        name: str,
        *,
        variables: Optional[Dict[str, Any]] = None,
        user_text: Optional[str] = None,
        extra_messages: Optional[Sequence[Message]] = None,
        default_system: Optional[str] = None,
        default_user_prefix: str = "",
    ) -> Tuple[List[Message], PromptMeta]:
        """
        將模板渲染成 messages。
        - 若模板有 ---system/---user 分段：依分段產生 messages
        - 若模板無分段：
            - 系統訊息：使用模板全文（或 default_system）
            - 使用者訊息：由 user_text 產生（若提供）
        - extra_messages：可附加在最後（例如 KG evidence / tool results）

        回傳：(messages, meta)
        """
        raw, meta = self.load(name)
        variables = dict(variables or {})
        if user_text is not None and "user_text" not in variables:
            variables["user_text"] = user_text

        rendered = self._substitute(raw, variables)

        sections = self._split_by_roles(rendered)

        messages: List[Message] = []
        if sections:
            # 依模板分段建立
            for role, content in sections:
                if role not in self.ROLE_MARKERS:
                    raise PromptTemplateError(f"Unsupported role: {role}")
                content = content.strip()
                if content:
                    messages.append({"role": role, "content": content})
        else:
            # 無 role 分段：整份視為 system
            sys_content = rendered.strip()
            if not sys_content and default_system:
                sys_content = default_system.strip()
            if sys_content:
                messages.append({"role": "system", "content": sys_content})

            if user_text is not None:
                u = (default_user_prefix + user_text).strip()
                if u:
                    messages.append({"role": "user", "content": u})

        if extra_messages:
            messages.extend(list(extra_messages))

        # 更新 meta.roles（若原本沒分段，meta.roles 可能空）
        if not meta.roles:
            roles = tuple(m["role"] for m in messages if "role" in m)
            meta = PromptMeta(name=meta.name, version=meta.version, path=meta.path, roles=roles)

        return messages, meta

    # -------------------------
    # Internal helpers
    # -------------------------

    def _infer_version(self, stem: str) -> Optional[str]:
        """
        從檔名推版本：xxx_v1 -> v1
        """
        m = re.search(r"(_v\d+)$", stem)
        return m.group(1).lstrip("_") if m else None

    def _substitute(self, text: str, variables: Dict[str, Any]) -> str:
        """
        簡單 {{var}} 替換。
        - 未提供的變數：保留原樣（避免渲染時直接炸掉）
        """
        def repl(match: re.Match) -> str:
            key = match.group(1).strip()
            if key in variables:
                v = variables[key]
                return "" if v is None else str(v)
            return match.group(0)

        return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", repl, text)

    def _peek_roles(self, text: str) -> List[str]:
        roles = []
        for m in re.finditer(r"^---\s*(\w+)\s*$", text, flags=re.MULTILINE):
            roles.append(m.group(1).strip().lower())
        return roles

    def _split_by_roles(self, text: str) -> List[Tuple[str, str]]:
        """
        將模板以 ---role 切分成多段。
        回傳 [(role, content), ...]
        """
        # 找所有 marker
        markers = list(re.finditer(r"^---\s*(\w+)\s*$", text, flags=re.MULTILINE))
        if not markers:
            return []

        sections: List[Tuple[str, str]] = []
        for i, m in enumerate(markers):
            role = m.group(1).strip().lower()
            start = m.end()
            end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            content = text[start:end]
            sections.append((role, content))
        return sections

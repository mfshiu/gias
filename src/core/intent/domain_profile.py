import re
from dataclasses import dataclass, field

@dataclass
class DomainProfile:
    """
    通用、可插拔的領域設定：
    - synonym_rules: regex rewrite 做輕量語彙正規化
    - action_alias: action_name -> trigger strings，提供 alias boost
    - slot_map: param_key -> [alt_slot_keys]，讓 LLM slot 鍵對應到 action param
    - enum_alias: param_key -> {顯示值: 實際值}，如「攤位」-> "booth"
    - env_subscriptions: 執行計畫期間向黑板訂閱的 topic pattern（如 "Zone/*/*"），
      變動事件會送進監測迴圈；空串列表示不監看環境
    - env_fact_queries: 重規劃時查詢的環境事實，name -> Cypher（經黑板代理查詢），
      結果會放進重規劃的 prompt
    """
    name: str = "generic"
    synonym_rules: list[tuple[str, str]] = field(default_factory=list)
    action_alias: dict[str, list[str]] = field(default_factory=dict)
    slot_map: dict[str, list[str]] = field(default_factory=dict)
    enum_alias: dict[str, dict[str, str]] = field(default_factory=dict)
    env_subscriptions: list[str] = field(default_factory=list)
    env_fact_queries: dict[str, str] = field(default_factory=dict)

    def normalize(self, text: str) -> str:
        t = (text or "").strip()
        t = re.sub(r"\s+", " ", t)

        for pat, repl in self.synonym_rules:
            try:
                t = re.sub(pat, repl, t, flags=re.IGNORECASE)
            except re.error:
                continue

        return t

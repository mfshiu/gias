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
    - entity_aliases: 實體 id -> 其他稱呼（如 "AI_Tech_Area" -> ["AI 展區"]），
      讓環境事件中的 id 能對應到計畫步驟裡的中文名稱
    - entity_parents: 實體 id -> 所在的上層實體（如攤位 -> 展區）；
      上層實體的事件也會影響引用下層實體的步驟
    - state_probes: 狀態變數名稱 -> Cypher（參數 $arg，回傳欄位 value），
      用來檢查 Action 的前置條件（見 src/core/intent/preconditions.py）
    - env_entity_keys: 環境事件 metadata 中另外代表實體的欄位（BlackboardWatcher 的 node_id、source_id 已內建），
      給其他事件來源使用
    """
    name: str = "generic"
    synonym_rules: list[tuple[str, str]] = field(default_factory=list)
    action_alias: dict[str, list[str]] = field(default_factory=dict)
    slot_map: dict[str, list[str]] = field(default_factory=dict)
    enum_alias: dict[str, dict[str, str]] = field(default_factory=dict)
    env_subscriptions: list[str] = field(default_factory=list)
    env_fact_queries: dict[str, str] = field(default_factory=dict)
    entity_aliases: dict[str, list[str]] = field(default_factory=dict)
    entity_parents: dict[str, str] = field(default_factory=dict)
    state_probes: dict[str, str] = field(default_factory=dict)
    env_entity_keys: list[str] = field(default_factory=list)

    def normalize(self, text: str) -> str:
        t = (text or "").strip()
        t = re.sub(r"\s+", " ", t)

        for pat, repl in self.synonym_rules:
            try:
                t = re.sub(pat, repl, t, flags=re.IGNORECASE)
            except re.error:
                continue

        return t

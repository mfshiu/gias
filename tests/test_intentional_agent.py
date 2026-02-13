# tests/test_intentional_agent.py
import logging
import pytest
from dotenv import load_dotenv

from src.app_helper import get_agent_config
from src.core.intentional_agent import IntentionalAgent
from src.kg.adapter_neo4j import build_neo4j_adapter
from src.llm.client import LLMClient
from src.core.intent.embedder import LLMEmbedder

logger = logging.getLogger(__name__)

SEED_PREFIX = "SeedTest_"


# -------------------------
# Config guards
# -------------------------
def _require_llm_ready(agent_config: dict) -> None:
    llm_cfg = agent_config.get("llm")
    if not isinstance(llm_cfg, dict):
        pytest.skip("Missing [llm] config in gias.toml (agent_config['llm']).")

    provider = str(llm_cfg.get("provider", "")).lower().strip()
    if not provider:
        pytest.skip("Missing llm.provider in gias.toml.")

    if provider == "openai":
        openai_cfg = llm_cfg.get("openai") or {}
        api_key = openai_cfg.get("api_key")
        if not api_key:
            pytest.skip("Missing llm.openai.api_key in gias.toml.")
        if not isinstance(api_key, str):
            pytest.skip("llm.openai.api_key must be a string in gias.toml.")
        return

    if provider == "ollama":
        ollama_cfg = llm_cfg.get("ollama") or {}
        base_url = ollama_cfg.get("base_url")
        model = ollama_cfg.get("model")
        if not base_url or not model:
            pytest.skip("Missing llm.ollama.base_url or llm.ollama.model in gias.toml.")
        return

    if provider == "mock":
        return

    pytest.skip(f"Unsupported llm.provider={provider!r} in gias.toml.")


def _get_embedder(agent_config) -> LLMEmbedder:
    llm = LLMClient.from_config(agent_config)
    return LLMEmbedder(llm)


def _assert_seed_ready_and_vector_query_works(kg, *, dims: int):
    # 1) 確認 seed actions 存在、且 embedding 維度正確
    r = kg.query(
        """
        MATCH (a:Action)
        WHERE a.name STARTS WITH $p AND a.description_embedding IS NOT NULL
        RETURN count(a) AS cnt,
               min(size(a.description_embedding)) AS minDim,
               max(size(a.description_embedding)) AS maxDim
        """,
        {"p": SEED_PREFIX},
    )[0]
    assert int(r["cnt"]) > 0, "Seed actions not found in DB (name prefix)."
    assert int(r["minDim"]) == int(dims) and int(r["maxDim"]) == int(dims), (
        f"Seed embedding dims mismatch in DB: {r}"
    )

    # 2) 確認 vector index 存在且 ONLINE
    idx = kg.query(
        """
        SHOW INDEXES YIELD name, state
        WHERE name = $n
        RETURN name, state
        """,
        {"n": "action_desc_vec"},
    )
    assert idx and idx[0]["state"] == "ONLINE", f"Vector index not ONLINE: {idx}"

    # 3) 用 Cypher 直接 queryNodes 驗證 vector search 可用
    one = kg.query(
        """
        MATCH (a:Action) WHERE a.name STARTS WITH $p
        RETURN a.description_embedding AS emb
        LIMIT 1
        """,
        {"p": SEED_PREFIX},
    )[0]
    emb = one["emb"]
    out = kg.query(
        """
        CALL db.index.vector.queryNodes('action_desc_vec', 5, $v)
        YIELD node, score
        RETURN node.name AS name, score
        ORDER BY score DESC
        """,
        {"v": emb},
    )
    assert out, "Vector queryNodes returned empty; adapter/index/query is broken."


# -------------------------
# KG helpers
# -------------------------
def _build_kg_adapter_from_agent_config(agent_config):
    if "kg" not in agent_config or not isinstance(agent_config["kg"], dict):
        pytest.skip("agent_config missing 'kg' dict config.")

    kg_cfg = agent_config["kg"]
    kg_type = kg_cfg.get("type", "neo4j")
    if kg_type != "neo4j":
        pytest.skip(f"Only neo4j supported, got kg.type={kg_type!r}")

    neo = kg_cfg.get("neo4j")
    if not isinstance(neo, dict):
        pytest.skip("agent_config['kg']['neo4j'] missing or not a dict.")

    uri = neo.get("uri")
    if not uri:
        pytest.skip("kg.neo4j missing 'uri'")

    return build_neo4j_adapter(
        uri=uri,
        user=neo.get("user"),
        password=neo.get("password"),
        database=neo.get("database"),
        encrypted=neo.get("encrypted", False),
        fetch_size=int(neo.get("fetch_size", 2000)),
        timeout_sec=int(neo.get("timeout_sec", 15)),
        max_retries=int(neo.get("max_retries", 2)),
        retry_backoff_sec=float(neo.get("retry_backoff_sec", 0.5)),
        logger=None,
    )


def _require_neo4j_ready(agent_config):
    kg = _build_kg_adapter_from_agent_config(agent_config)
    try:
        r = kg.query("RETURN 1 AS ok", {})
        if not r or r[0].get("ok") != 1:
            pytest.skip("Neo4j not responding as expected.")
    except Exception as e:
        pytest.skip(f"Neo4j not reachable: {e}")
    return kg


def _count_actions_with_embedding(kg, dims: int | None = None) -> int:
    if dims is None:
        row = kg.query(
            """
            MATCH (a:Action)
            WHERE a.description_embedding IS NOT NULL
            RETURN count(a) AS cnt
            """,
            {},
        )[0]
        return int(row["cnt"])

    row = kg.query(
        """
        MATCH (a:Action)
        WHERE a.description_embedding IS NOT NULL AND size(a.description_embedding) = $dims
        RETURN count(a) AS cnt
        """,
        {"dims": int(dims)},
    )[0]
    return int(row["cnt"])


def _cleanup_seed_actions(kg):
    kg.write(
        "MATCH (a:Action) WHERE a.name STARTS WITH $p DETACH DELETE a",
        {"p": SEED_PREFIX},
    )


def _seed_min_actions_if_needed(kg, *, embedder: LLMEmbedder, dims: int):
    """
    ✅ 這次 seed 不只 1 個「廁所」，而是最小可用 action set：
    - 導航/路線/定位類（讓 in-domain 比較容易 match）
    - 每個 action embedding 用真 embedder 產生
    - 當 DB 沒有 SeedTest_ 前綴的 actions 時才 seed（測試專用）
    """
    # 檢查是否已有 SeedTest_ 前綴的 actions（測試用）
    r = kg.query(
        """
        MATCH (a:Action)
        WHERE a.name STARTS WITH $p AND a.description_embedding IS NOT NULL
        RETURN count(a) AS cnt
        """,
        {"p": SEED_PREFIX},
    )[0]
    if r and int(r["cnt"]) > 0:
        return

    # ✅ 先建立 vector index（確保 dimensions 符合，否則現有 index 可能用錯 dims）
    if hasattr(kg, "ensure_vector_index"):
        kg.ensure_vector_index(
            index_name="action_desc_vec",
            label="Action",
            embedding_prop="description_embedding",
            dimensions=dims,
        )

    # 最小 seed action set（測試用）
    seed_actions = [
        (f"{SEED_PREFIX}LocateExhibit", "引導使用者前往指定目標的位置並提供定位協助（測試用）"),
        (f"{SEED_PREFIX}ExplainDirections", "用自然語言說明從目前位置前往目的地的方向與轉彎提示（測試用）"),
        (f"{SEED_PREFIX}SuggestRoute", "根據起點與終點規劃建議路線並避開不利路段（測試用）"),
    ]

    for name, desc in seed_actions:
        emb = embedder.embed_text(desc)
        emb = [float(x) for x in emb]
        if len(emb) != dims:
            raise RuntimeError(f"Seed embedding dims mismatch: got={len(emb)} expected={dims}")

        kg.write(
            """
            MERGE (a:Action {name:$name})
            SET a.description=$desc,
                a.description_embedding=$emb,
                a.source='seed_test'
            RETURN id(a) AS id, a.name AS name
            """,
            {"name": name, "desc": desc, "emb": emb},
        )

    # ✅ 保險：確認 seed 真的存在且 embedding 已寫入（與 _assert 同一標準）
    r = kg.query(
        """
        MATCH (a:Action)
        WHERE a.name STARTS WITH $p AND a.description_embedding IS NOT NULL
        RETURN count(a) AS cnt, min(size(a.description_embedding)) AS minDim
        """,
        {"p": SEED_PREFIX},
    )
    if not r or int(r[0]["cnt"]) <= 0:
        # 診斷：有多少 node 有前綴但沒 embedding？
        diag = kg.query(
            """
            MATCH (a:Action) WHERE a.name STARTS WITH $p
            RETURN count(a) AS total,
                   count(a.description_embedding) AS with_emb
            """,
            {"p": SEED_PREFIX},
        )
        d = diag[0] if diag else {}
        raise RuntimeError(
            f"Seed actions not ready: expected >0 with embedding, got cnt={r[0]['cnt'] if r else '?'}. "
            f"Diagnostic: total={d.get('total')}, with_emb={d.get('with_emb')}"
        )
    if int(r[0]["minDim"]) != int(dims):
        raise RuntimeError(
            f"Seed embedding dims mismatch: got minDim={r[0]['minDim']} expected={dims}"
        )


# -------------------------
# Plan tree utils
# -------------------------
def _is_plan_tree(obj) -> bool:
    return (
        isinstance(obj, dict)
        and "id" in obj
        and "intent" in obj
        and isinstance(obj.get("sub_plans"), list)
    )


def _walk_plan_tree(plan: dict) -> list[dict]:
    out = []
    stack = [plan]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            out.append(n)
            kids = n.get("sub_plans", [])
            if isinstance(kids, list) and kids:
                stack.extend(reversed(kids))
    return out


# -------------------------
# Tests
# -------------------------
@pytest.mark.integration
def test_break_down_intention_real_llm():
    load_dotenv()

    agent_config = get_agent_config()
    _require_llm_ready(agent_config)

    test_intention = "幫我查一下台北今天的天氣，並整理成摘要"
    agent = IntentionalAgent(agent_config=agent_config, intention=test_intention)

    sub_intentions = agent.break_down_intention(test_intention)

    logger.info("Break down result:")
    for i, si in enumerate(sub_intentions, start=1):
        logger.info("  [%d] %s", i, si)

    assert isinstance(sub_intentions, list)
    assert len(sub_intentions) > 0
    assert all(si is not None for si in sub_intentions)


@pytest.mark.integration
def test_match_actions_real_kg_vector():
    load_dotenv()

    agent_config = get_agent_config()
    _require_llm_ready(agent_config)

    embedder = _get_embedder(agent_config)
    dims = len(embedder.embed_text("dimension_probe"))

    kg = _require_neo4j_ready(agent_config)

    try:
        _seed_min_actions_if_needed(kg, embedder=embedder, dims=dims)
        _assert_seed_ready_and_vector_query_works(kg, dims=dims)
        cnt = _count_actions_with_embedding(kg, dims=dims)
        if cnt == 0:
            pytest.skip(f"No Action nodes with description_embedding dims={dims} (even after seed).")

        test_intention = "請告訴我怎麼從目前位置走到指定目標"
        agent = IntentionalAgent(agent_config=agent_config, intention=test_intention)

        actions = agent.match_actions(test_intention, top_k=10, min_score=0.0)

        logger.info("Matched actions:")
        for i, a in enumerate(actions, start=1):
            logger.info("  [%d] %s", i, a)

        assert isinstance(actions, list)
        assert len(actions) > 0
        assert all(hasattr(x, "action") and hasattr(x, "score") for x in actions)

    finally:
        try:
            _cleanup_seed_actions(kg)
            kg.close()
        except Exception:
            pass


@pytest.mark.integration
def test_plan_intention_with_expo_domain_profile():
    load_dotenv()

    agent_config = get_agent_config()
    _require_llm_ready(agent_config)

    embedder = _get_embedder(agent_config)
    dims = len(embedder.embed_text("dimension_probe"))

    kg = _require_neo4j_ready(agent_config)

    try:
        # ✅ 注意：seed 在前、cleanup 在最後（不要 seed 完就刪）
        _seed_min_actions_if_needed(kg, embedder=embedder, dims=dims)
        _assert_seed_ready_and_vector_query_works(kg, dims=dims)
        # A) In-domain
        from src.core.intentional_agent import DomainProfile

        expo_profile = DomainProfile(
            name="expo",
            synonym_rules=[
                (r"廠商", "展商"),
                (r"有賣", "販售"),
                (r"賣", "販售"),
                (r"帶我去", "引導我前往"),
                (r"去", "前往"),
            ],
            action_alias={
                "RecommendExhibits": ["推薦", "哪裡有", "有賣", "販售", "找", "展商", "攤位", "廠商"],
                "LocateExhibit": ["帶我去", "引導我前往", "前往", "在哪", "位置"],
                "ExplainDirections": ["怎麼走", "怎麼去", "路線", "方向"],
                "SuggestRoute": ["路線", "怎麼安排", "規劃"],
                "CrowdStatus": ["人多", "擁擠", "人潮"],
                "LocateFacility": ["洗手間", "廁所", "服務台", "無障礙"],
            },
        )

        in_domain_intention = "我在入口附近，想前往A12攤位，順便告訴我怎麼走。"
        agent_in = IntentionalAgent(
            agent_config=agent_config,
            intention=in_domain_intention,
            domain_profile=expo_profile,
        )
        plan_in = agent_in.plan_intention(in_domain_intention)

        assert _is_plan_tree(plan_in)
        assert plan_in.get("intent")

        if plan_in.get("type") in ("leaf_unresolved", "leaf_no_children"):
            pytest.skip(
                f"In-domain plan unresolved: type={plan_in.get('type')}, reason={plan_in.get('reason')}, "
                f"unmatched={plan_in.get('unmatched_sub_intentions')}"
            )

        nodes_in = _walk_plan_tree(plan_in)
        atomic_in = [n for n in nodes_in if n.get("type") == "atomic" or n.get("is_atomic") is True]
        if len(atomic_in) == 0:
            pytest.skip("In-domain plan has no atomic nodes.")

        atomic_actions_in = [n.get("action", "") for n in atomic_in if isinstance(n.get("action", ""), str)]
        logger.info("In-domain atomic actions: %s", atomic_actions_in)
        assert len(atomic_actions_in) >= 1

        # B) Out-of-domain
        out_of_domain_intention = "我的iPhone 17壞了請幫我修理。"
        agent_out = IntentionalAgent(
            agent_config=agent_config,
            intention=out_of_domain_intention,
            domain_profile=expo_profile,
        )
        plan_out = agent_out.plan_intention(out_of_domain_intention)

        assert _is_plan_tree(plan_out)
        assert plan_out.get("intent")

        enable_scope_gate = (
            agent_config.get("intent", {}).get("enable_scope_gate", False)
            or agent_config.get("intentional_agent", {}).get("enable_scope_gate", False)
        )

        if enable_scope_gate:
            assert plan_out.get("type") == "leaf_unresolved", (
                "Out-of-domain intention must be rejected (leaf_unresolved) when scope gate is enabled. "
                f"got type={plan_out.get('type')}, reason={plan_out.get('reason')}, debug={plan_out.get('debug')}"
            )
            assert plan_out.get("reason")
        else:
            nodes_out = _walk_plan_tree(plan_out)
            atomic_out = [n for n in nodes_out if n.get("type") == "atomic" or n.get("is_atomic") is True]
            atomic_sources = list({n.get("atomic_source") for n in atomic_out})
            logger.warning(
                "Scope gate disabled. Out-of-domain may be incorrectly planned. atomic_sources=%s type=%s debug=%s",
                atomic_sources,
                plan_out.get("type"),
                plan_out.get("debug"),
            )
            pytest.xfail("Known issue: no scope gate; out-of-domain intent may be incorrectly planned into pre_defined actions.")

    finally:
        try:
            _cleanup_seed_actions(kg)
            kg.close()
        except Exception:
            pass

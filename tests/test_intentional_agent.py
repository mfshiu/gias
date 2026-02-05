# tests/test_intentional_agent.py
import os
import logging
import pytest
from dotenv import load_dotenv

from src.intentional_agent import IntentionalAgent
from src.app_helper import get_agent_config
from src.kg.adapter_neo4j import build_neo4j_adapter

logger = logging.getLogger(__name__)


def _require_openai_key():
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY")):
        pytest.skip("Missing LLM API key env var (e.g., OPENAI_API_KEY).")


def _build_kg_adapter_from_agent_config(agent_config):
    """
    從 gias.toml 對應結構建立 Neo4j adapter，但「不覆寫」agent_config["kg"]。
    只用於測試前置檢查（ping / count / seed）。
    """
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
        fetch_size=kg_cfg.get("fetch_size", 2000),
        timeout_sec=kg_cfg.get("timeout_sec", 15),
        max_retries=kg_cfg.get("max_retries", 2),
        retry_backoff_sec=kg_cfg.get("retry_backoff_sec", 0.5),
        logger=None,
    )


def _require_neo4j_ready(agent_config):
    """
    只做連線檢查，不改動 agent_config 結構（避免 IntentionalAgent.kg property 失效）。
    """
    kg = _build_kg_adapter_from_agent_config(agent_config)
    try:
        r = kg.read("RETURN 1 AS ok")
        if not r or r[0].get("ok") != 1:
            pytest.skip("Neo4j not responding as expected.")
    except Exception as e:
        pytest.skip(f"Neo4j not reachable: {e}")
    return kg


def _count_actions_with_embedding(kg):
    res = kg.read(
        """
        MATCH (a:Action)
        WHERE a.description_embedding IS NOT NULL
        RETURN count(a) AS c
        """
    )
    return int(res[0]["c"]) if res else 0


def _seed_one_action_with_embedding_if_needed(kg, *, dims: int = 8):
    """
    測試用：若 KG 內沒有任何 Action.description_embedding，塞一筆最小可用的資料，
    讓 vector search / match_actions 不會整個 skip。

    注意：這會改動測試用 Neo4j。建議把 integration 測試指向測試資料庫。
    """
    cnt = _count_actions_with_embedding(kg)
    if cnt > 0:
        return

    # Neo4j 向量索引通常吃 List<Float>。用簡單 deterministic 向量即可。
    emb = [0.01 * (i + 1) for i in range(dims)]

    kg.write(
        """
        MERGE (a:Action {name:$name})
        SET a.description=$desc,
            a.description_embedding=$emb
        RETURN a
        """,
        {
            "name": "TestAction_WC",
            "desc": "指引使用者找到洗手間的路徑與方向（測試用）",
            "emb": emb,
        },
    )


@pytest.mark.integration
def test_break_down_intention_real_llm():
    load_dotenv()
    _require_openai_key()

    test_intention = "幫我查一下台北今天的天氣，並整理成摘要"

    agent = IntentionalAgent(
        agent_config=get_agent_config(),
        intention=test_intention,
    )

    sub_intentions = agent.break_down_intention(test_intention)

    logger.info("Break down result:")
    for i, si in enumerate(sub_intentions, start=1):
        logger.info("  [%d] %s", i, si)

    assert isinstance(sub_intentions, list)
    assert len(sub_intentions) > 0
    assert all(isinstance(x, str) for x in sub_intentions)


@pytest.mark.integration
def test_match_actions_real_kg_vector():
    load_dotenv()
    _require_openai_key()

    agent_config = get_agent_config()

    # 先用「獨立 adapter」確認 Neo4j 可連線（但不改 agent_config）
    kg = _require_neo4j_ready(agent_config)

    test_intention = "洗手間怎麼走？"

    # ✅ 讓 IntentionalAgent 依照它原本的 kg property/config 流程建立 KG
    agent = IntentionalAgent(
        agent_config=agent_config,
        intention=test_intention,
    )

    try:
        # 先確保 KG 內至少有一筆 embedding，避免整個測試永遠 skip
        _seed_one_action_with_embedding_if_needed(kg, dims=8)

        # 再確認一次（如果你的系統要求一定要有既有資料，也會在這裡看出來）
        cnt = _count_actions_with_embedding(kg)
        if cnt == 0:
            pytest.skip("No Action nodes with description_embedding in KG (even after seed).")

    except Exception as e:
        pytest.skip(f"Cannot prepare Action nodes: {e}")
    finally:
        try:
            kg.close()
        except Exception:
            pass

    actions = agent.match_actions(
        test_intention,
        top_k=10,
        min_score=0.70,
    )

    logger.info("Matched actions:")
    for i, a in enumerate(actions, start=1):
        logger.info("  [%d] %s", i, a)

    assert isinstance(actions, list)
    assert len(actions) > 0
    assert all(isinstance(x, str) for x in actions)


@pytest.mark.integration
def test_plan_intention_real_llm_and_kg():
    """
    ✅ 你要的：完整走 LLM + KG
    - plan_intention() 會先 break_down_intention()（LLM）
    - 對每個 sub_intention 呼叫 match_actions()（會觸發 embedding + KG vector search）
    - 最終回傳 plan（目前版本是固定格式）
    """
    load_dotenv()
    _require_openai_key()

    agent_config = get_agent_config()

    # 先確認 Neo4j 可用；並確保至少有一筆 action embedding（避免永遠 skip）
    kg = _require_neo4j_ready(agent_config)
    try:
        _seed_one_action_with_embedding_if_needed(kg, dims=8)
        cnt = _count_actions_with_embedding(kg)
        if cnt == 0:
            pytest.skip("No Action nodes with description_embedding in KG (even after seed).")
    except Exception as e:
        pytest.skip(f"Neo4j preparation failed: {e}")
    finally:
        try:
            kg.close()
        except Exception:
            pass

    test_intention = """我現在在會場入口，想參觀有趣的展區，可以推薦幾個適合我的展品，
順便告訴我怎麼走、路線怎麼安排，現在人會不會很多，途中如果有洗手間或服務台也請提醒我嗎？"""

    agent = IntentionalAgent(
        agent_config=agent_config,
        intention=test_intention,
    )

    plan = agent.plan_intention(test_intention)

    logger.info("Plan result:")
    for i, p in enumerate(plan, start=1):
        logger.info("  [%d] %s", i, p)

    # plan_intention 目前回傳固定格式（未把 actions 寫進 plan）
    assert isinstance(plan, list)
    assert plan == [f"Execute step for: {test_intention}"]

# 重試/逾時/配額/成本上限/降級策略
#   重試策略（退避）
#   成本上限（max tokens / budget）
#   fallback（強模型失敗→弱模型、或改兩段式）
#   逾時控制
#   配額控制（rate limit / daily limit）
# 抽象成 Policy，讓不同任務可以共用
# 不同任務可以有不同 Policy 組合
# Policy 可以套用在不同 LLMClient 上
# Policy 可以獨立測試
# Policy 只負責「控制邏輯」，不直接呼叫 LLM API
class Policy:
    def __init__(self, retries=3, timeout=10, max_cost=None, fallback_client=None):
        self.retries = retries
        self.timeout = timeout
        self.max_cost = max_cost
        self.fallback_client = fallback_client

    def apply(self, llm_client, *args, **kwargs):
        attempt = 0
        total_cost = 0

        while attempt < self.retries:
            try:
                response = llm_client.call(*args, timeout=self.timeout, **kwargs)
                total_cost += response.cost

                if self.max_cost and total_cost > self.max_cost:
                    raise Exception("Exceeded maximum cost limit")

                return response

            except Exception as e:
                attempt += 1
                if attempt >= self.retries and self.fallback_client:
                    return self.fallback_client.call(*args, timeout=self.timeout, **kwargs)

        raise Exception("All attempts failed")
    
# 請求快取（hash key）
#   快取 key：task + prompt_version + normalized_input_hash + model
#   先做 in-memory，之後可換 Redis
#   減少重複請求，降低成本與延遲



class LLMCache:
    def __init__(self):
        self.cache = {}

    def _generate_key(self, task, prompt_version, input_hash, model):
        return f"{task}:{prompt_version}:{input_hash}:{model}"

    def get(self, task, prompt_version, input_hash, model):
        key = self._generate_key(task, prompt_version, input_hash, model)
        return self.cache.get(key)

    def set(self, task, prompt_version, input_hash, model, response):
        key = self._generate_key(task, prompt_version, input_hash, model)
        self.cache[key] = response
        
    def clear(self):
        self.cache = {}
        


llm_cache = LLMCache()

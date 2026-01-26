# model routing：依任務選模型（fast vs strong）
#   依任務選模型：FAST_MODEL / REASONING_MODEL
#   也可依 payload 大小、重要性切換
#   例如簡單問答用 fast 模型，複雜推理用強模型
#   未來可擴充成多模型路由
#   讓任務層不需關心模型細節

class ModelRouter:
    def __init__(self, fast_model_client, strong_model_client):
        self.fast_model_client = fast_model_client
        self.strong_model_client = strong_model_client

    def route(self, task_type, payload):
        if task_type == "simple_query":
            return self.fast_model_client
        elif task_type == "complex_reasoning":
            return self.strong_model_client
        else:
            # Default to strong model for unknown task types
            return self.strong_model_client

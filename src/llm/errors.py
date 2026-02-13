# src/llm/errors.py

class LLMError(RuntimeError):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMRateLimitError(LLMError):
    pass


class LLMInvalidJSONError(LLMError):
    pass


class LLMSchemaValidationError(LLMError):
    pass


class LLMEmbeddingNotSupportedError(LLMError):
    pass

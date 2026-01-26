# trace/log/metrics（統一格式）
#   統一 log 欄位：trace_id、task、model、prompt_version、latency、tokens、retry_count、error_type
#   你現有 log_helper.py 可以在這裡整合
import logging
logger = logging.getLogger("llm_observability")
def log_request(trace_id, task, model, prompt_version, latency, tokens, retry_count, error_type=None):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "latency": latency,
        "tokens": tokens,
        "retry_count": retry_count,
    }
    if error_type:
        log_data["error_type"] = error_type
        logger.error(f"LLM Request Failed: {log_data}")
    else:
        logger.info(f"LLM Request Success: {log_data}")
def log_metric(metric_name, value, tags=None):
    tags_str = ",".join([f"{k}:{v}" for k, v in (tags or {}).items()])
    logger.info(f"Metric - {metric_name}: {value} | Tags: {tags_str}")
def log_error(trace_id, task, model, prompt_version, error):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "error": str(error),
    }
    logger.error(f"LLM Error: {log_data}")
def log_latency(trace_id, task, model, prompt_version, latency):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "latency": latency,
    }
    logger.info(f"LLM Latency: {log_data}")
def log_token_usage(trace_id, task, model, prompt_version, tokens):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "tokens": tokens,
    }
    logger.info(f"LLM Token Usage: {log_data}")
def log_retry(trace_id, task, model, prompt_version, retry_count):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "retry_count": retry_count,
    }
    logger.info(f"LLM Retry: {log_data}")
def log_fallback(trace_id, task, from_model, to_model):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "from_model": from_model,
        "to_model": to_model,
    }
    logger.warning(f"LLM Fallback: {log_data}")
def log_quota_exceeded(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.error(f"LLM Quota Exceeded: {log_data}")
def log_timeout(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.error(f"LLM Timeout: {log_data}")
def log_cost(trace_id, task, model, prompt_version, cost):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "cost": cost,
    }
    logger.info(f"LLM Cost: {log_data}")
def log_budget_exceeded(trace_id, task, model, prompt_version, budget):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "budget": budget,
    }
    logger.error(f"LLM Budget Exceeded: {log_data}")
def log_degradation(trace_id, task, from_model, to_model):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "from_model": from_model,
        "to_model": to_model,
    }
    logger.warning(f"LLM Degradation: {log_data}")
def log_request_start(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.info(f"LLM Request Start: {log_data}")
def log_request_end(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.info(f"LLM Request End: {log_data}")
def log_cache_hit(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.info(f"LLM Cache Hit: {log_data}")
def log_cache_miss(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.info(f"LLM Cache Miss: {log_data}")
def log_cache_set(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.info(f"LLM Cache Set: {log_data}")
def log_cache_clear():
    logger.info("LLM Cache Cleared")
def log_rate_limit(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.error(f"LLM Rate Limit Exceeded: {log_data}")
def log_daily_limit(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.error(f"LLM Daily Limit Exceeded: {log_data}")
def log_model_switch(trace_id, task, from_model, to_model):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "from_model": from_model,
        "to_model": to_model,
    }
    logger.warning(f"LLM Model Switch: {log_data}")
def log_prompt_version(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.info(f"LLM Prompt Version Used: {log_data}")
def log_input_normalization(trace_id, task, model, prompt_version, input_hash):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "input_hash": input_hash,
    }
    logger.info(f"LLM Input Normalization: {log_data}")
def log_input_denormalization(trace_id, task, model, prompt_version, input_hash):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "input_hash": input_hash,
    }
    logger.info(f"LLM Input Denormalization: {log_data}")
def log_response_validation(trace_id, task, model, prompt_version, is_valid):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "is_valid": is_valid,
    }
    logger.info(f"LLM Response Validation: {log_data}")
def log_response_correction(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.info(f"LLM Response Correction Attempted: {log_data}")
def log_response_correction_success(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.info(f"LLM Response Correction Succeeded: {log_data}")
def log_response_correction_failure(trace_id, task, model, prompt_version):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
    }
    logger.error(f"LLM Response Correction Failed: {log_data}")
    
def log_fallback_attempt(trace_id, task, from_model, to_model):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "from_model": from_model,
        "to_model": to_model,
    }
    logger.warning(f"LLM Fallback Attempted: {log_data}")
def log_fallback_success(trace_id, task, from_model, to_model):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "from_model": from_model,
        "to_model": to_model,
    }
    logger.info(f"LLM Fallback Succeeded: {log_data}")
def log_fallback_failure(trace_id, task, from_model, to_model):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "from_model": from_model,
        "to_model": to_model,
    }
    logger.error(f"LLM Fallback Failed: {log_data}")
def log_policy_application(trace_id, task, model, prompt_version, policy_details):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
    }
    logger.info(f"LLM Policy Applied: {log_data}")
    
def log_policy_failure(trace_id, task, model, prompt_version, policy_details, error):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
        "error": str(error),
    }
    logger.error(f"LLM Policy Failed: {log_data}")
def log_policy_success(trace_id, task, model, prompt_version, policy_details):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
    }
    logger.info(f"LLM Policy Succeeded: {log_data}")
    
def log_policy_retry(trace_id, task, model, prompt_version, policy_details, retry_count):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
        "retry_count": retry_count,
    }
    logger.info(f"LLM Policy Retry: {log_data}")
def log_policy_timeout(trace_id, task, model, prompt_version, policy_details):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
    }
    logger.error(f"LLM Policy Timeout: {log_data}")
    
def log_policy_quota_exceeded(trace_id, task, model, prompt_version, policy_details):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
    }
    logger.error(f"LLM Policy Quota Exceeded: {log_data}")
def log_policy_budget_exceeded(trace_id, task, model, prompt_version, policy_details, budget):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
        "budget": budget,
    }
    logger.error(f"LLM Policy Budget Exceeded: {log_data}")
def log_policy_fallback(trace_id, task, from_model, to_model, policy_details):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "from_model": from_model,
        "to_model": to_model,
        "policy_details": policy_details,
    }
    logger.warning(f"LLM Policy Fallback: {log_data}")
    
def log_policy_degradation(trace_id, task, from_model, to_model, policy_details):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "from_model": from_model,
        "to_model": to_model,
        "policy_details": policy_details,
    }
    logger.warning(f"LLM Policy Degradation: {log_data}")
    
def log_policy_application_start(trace_id, task, model, prompt_version, policy_details):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
    }
    logger.info(f"LLM Policy Application Start: {log_data}")
    
def log_policy_application_end(trace_id, task, model, prompt_version, policy_details):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
    }
    logger.info(f"LLM Policy Application End: {log_data}")
    
def log_policy_evaluation(trace_id, task, model, prompt_version, policy_details, evaluation_result):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
        "evaluation_result": evaluation_result,
    }
    logger.info(f"LLM Policy Evaluation: {log_data}")
    
def log_policy_adjustment(trace_id, task, model, prompt_version, policy_details, adjustment_details):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
        "adjustment_details": adjustment_details,
    }
    logger.info(f"LLM Policy Adjustment: {log_data}")
    
def log_policy_enforcement(trace_id, task, model, prompt_version, policy_details):
    log_data = {
        "trace_id": trace_id,
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "policy_details": policy_details,
    }
    logger.info(f"LLM Policy Enforcement: {log_data}")
    
    
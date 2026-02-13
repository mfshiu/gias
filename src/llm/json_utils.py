# src/llm/json_utils.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from llm.errors import LLMInvalidJSONError, LLMSchemaValidationError
from llm.types import JsonType, SchemaType


def parse_json(content: str, *, strict_json: bool) -> JsonType:
    text = (content or "").strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    fenced = extract_fenced_json(text)
    if fenced is not None:
        try:
            return json.loads(fenced)
        except Exception:
            pass

    candidate = extract_first_json_object(text)
    if candidate is not None:
        try:
            return json.loads(candidate)
        except Exception:
            pass

    if strict_json:
        raise LLMInvalidJSONError("Model output is not valid JSON.")
    return {"_raw": text}


def extract_fenced_json(text: str) -> Optional[str]:
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def extract_first_json_object(text: str) -> Optional[str]:
    start_positions = [(text.find("{"), "{"), (text.find("["), "[")]
    start_positions = [(pos, ch) for pos, ch in start_positions if pos != -1]
    if not start_positions:
        return None

    pos, ch = min(start_positions, key=lambda x: x[0])
    closing = "}" if ch == "{" else "]"

    depth = 0
    in_str = False
    esc = False

    for i in range(pos, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue

        if c == '"':
            in_str = True
            continue

        if c == ch:
            depth += 1
        elif c == closing:
            depth -= 1
            if depth == 0:
                return text[pos : i + 1].strip()

    return None


def validate_schema(obj: Any, schema: SchemaType) -> Any:
    if schema is None:
        return obj

    # callable validator
    if callable(schema) and not isinstance(schema, dict) and not isinstance(schema, type):
        try:
            return schema(obj)
        except Exception as e:
            raise LLMSchemaValidationError(str(e)) from e

    # pydantic-like class
    if isinstance(schema, type):
        if hasattr(schema, "model_validate"):
            try:
                return schema.model_validate(obj)  # type: ignore[attr-defined]
            except Exception as e:
                raise LLMSchemaValidationError(str(e)) from e
        if hasattr(schema, "parse_obj"):
            try:
                return schema.parse_obj(obj)  # type: ignore[attr-defined]
            except Exception as e:
                raise LLMSchemaValidationError(str(e)) from e

    # json schema minimal
    if isinstance(schema, dict):
        try:
            validate_json_schema_minimal(obj, schema)
            return obj
        except Exception as e:
            raise LLMSchemaValidationError(str(e)) from e

    raise LLMSchemaValidationError(f"Unsupported schema type: {type(schema)}")


def validate_json_schema_minimal(obj: Any, schema: Dict[str, Any]) -> None:
    expected_type = schema.get("type")
    if expected_type:
        if expected_type == "object" and not isinstance(obj, dict):
            raise ValueError("Expected object.")
        if expected_type == "array" and not isinstance(obj, list):
            raise ValueError("Expected array.")
        if expected_type == "string" and not isinstance(obj, str):
            raise ValueError("Expected string.")
        if expected_type == "number" and not isinstance(obj, (int, float)):
            raise ValueError("Expected number.")
        if expected_type == "integer" and not isinstance(obj, int):
            raise ValueError("Expected integer.")
        if expected_type == "boolean" and not isinstance(obj, bool):
            raise ValueError("Expected boolean.")

    if isinstance(obj, dict):
        required = schema.get("required") or []
        for k in required:
            if k not in obj:
                raise ValueError(f"Missing required field: {k}")

        props = schema.get("properties") or {}
        for k, sub_schema in props.items():
            if k in obj and isinstance(sub_schema, dict):
                validate_json_schema_minimal(obj[k], sub_schema)

    if isinstance(obj, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for item in obj:
                validate_json_schema_minimal(item, item_schema)

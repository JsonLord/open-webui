"""Validation and policy for final, post-Headroom Spark requests."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Any

MODEL_ID = "spark-x2.5-1.7b"
PHYSICAL_CONTEXT_LIMIT = 32768
ALLOWED_FIELDS = {
    "model",
    "messages",
    "stream",
    "max_tokens",
    "max_completion_tokens",
    "temperature",
    "top_p",
    "stop",
}
RETRYABLE_STATUS = {502, 503, 504}


class ContractError(ValueError):
    def __init__(self, status: int, code: str, message: str, **metadata: Any):
        super().__init__(message)
        self.status = status
        self.payload = {"error": code, "message": message, **metadata}


@dataclass(frozen=True)
class Settings:
    model: str = MODEL_ID
    context_limit: int = 32768
    max_input_tokens: int = 24576
    max_output_tokens: int = 4096
    context_reserve: int = 4096
    temperature: float = 0.2
    top_p: float = 0.95
    connect_timeout: float = 15.0
    read_timeout: float = 300.0
    max_retries: int = 1

    @classmethod
    def from_env(cls) -> "Settings":
        value = cls(
            model=os.getenv("SPARK_MODEL", MODEL_ID),
            context_limit=int(os.getenv("SPARK_CONTEXT_LIMIT", "32768")),
            max_input_tokens=int(os.getenv("SPARK_MAX_INPUT_TOKENS", "24576")),
            max_output_tokens=int(os.getenv("SPARK_MAX_OUTPUT_TOKENS", "4096")),
            context_reserve=int(os.getenv("SPARK_CONTEXT_RESERVE", "4096")),
            temperature=float(os.getenv("SPARK_TEMPERATURE", "0.2")),
            top_p=float(os.getenv("SPARK_TOP_P", "0.95")),
            connect_timeout=float(os.getenv("SPARK_CONNECT_TIMEOUT", "15")),
            read_timeout=float(os.getenv("SPARK_READ_TIMEOUT", "300")),
            max_retries=int(os.getenv("SPARK_MAX_RETRIES", "1")),
        )
        if value.model != MODEL_ID:
            raise ValueError(f"SPARK_MODEL must be {MODEL_ID!r}, got {value.model!r}")
        if value.context_limit > PHYSICAL_CONTEXT_LIMIT:
            raise ValueError(f"SPARK_CONTEXT_LIMIT cannot exceed the physical ceiling {PHYSICAL_CONTEXT_LIMIT}")
        if min(value.context_limit, value.max_input_tokens, value.max_output_tokens, value.context_reserve) < 0:
            raise ValueError("Spark token budgets cannot be negative")
        if value.max_input_tokens + value.max_output_tokens + value.context_reserve > value.context_limit:
            raise ValueError("configured Spark input/output/reserve budgets exceed context limit")
        if value.max_retries < 0 or value.max_retries > 3:
            raise ValueError("SPARK_MAX_RETRIES must be between 0 and 3")
        if value.connect_timeout <= 0 or value.read_timeout <= 0:
            raise ValueError("Spark connect and read timeouts must be positive")
        if not 0 <= value.temperature <= 2:
            raise ValueError("SPARK_TEMPERATURE must be between 0 and 2")
        if not 0 < value.top_p <= 1:
            raise ValueError("SPARK_TOP_P must be greater than 0 and at most 1")
        return value


def estimate_input_tokens(messages: list[dict[str, Any]]) -> int:
    """Conservative fallback: UTF-8 bytes/3 plus framing; not an exact tokenizer."""
    total = 3
    for message in messages:
        # Serializing all values counts tool call/result structures too.
        total += 12 + math.ceil(len(json.dumps(message, ensure_ascii=False).encode()) / 3)
    return total


def validate_request(
    payload: Any, settings: Settings, *, estimated_tokens: int | None = None
) -> tuple[dict[str, Any], int]:
    if not isinstance(payload, dict):
        raise ContractError(400, "spark_invalid_request", "request body must be a JSON object")
    unsupported = sorted(set(payload) - ALLOWED_FIELDS)
    if unsupported:
        raise ContractError(
            400, "spark_unsupported_parameters", "unsupported request parameters", parameters=unsupported
        )
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ContractError(400, "spark_invalid_request", "messages must be a non-empty array")
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("role"), str) or "content" not in message:
            raise ContractError(400, "spark_invalid_request", "each message requires role and content")

    requested_model = payload.get("model")
    if requested_model not in (None, "", settings.model, "spark-worker"):
        raise ContractError(
            400,
            "spark_model_mismatch",
            "request model does not match configured Spark",
            requested_model=requested_model,
            configured_model=settings.model,
        )

    if "max_tokens" in payload and "max_completion_tokens" in payload:
        raise ContractError(400, "spark_invalid_request", "use only one output-token field")
    requested = payload.get("max_tokens", payload.get("max_completion_tokens", settings.max_output_tokens))
    if not isinstance(requested, int) or isinstance(requested, bool) or requested <= 0:
        raise ContractError(400, "spark_invalid_request", "max_tokens must be a positive integer")
    output_tokens = min(requested, settings.max_output_tokens)
    estimate = estimate_input_tokens(messages) if estimated_tokens is None else estimated_tokens
    if (
        estimate > settings.max_input_tokens
        or estimate + output_tokens + settings.context_reserve > settings.context_limit
    ):
        raise ContractError(
            413,
            "spark_context_budget_exceeded",
            "post-compression request exceeds the Spark context budget",
            estimated_input_tokens=estimate,
            requested_output_tokens=output_tokens,
            reserve_tokens=settings.context_reserve,
            context_limit=settings.context_limit,
            max_input_tokens=settings.max_input_tokens,
        )

    clean: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
        "stream": payload.get("stream", False),
        "max_tokens": output_tokens,
        "temperature": payload.get("temperature", settings.temperature),
        "top_p": payload.get("top_p", settings.top_p),
    }
    if not isinstance(clean["stream"], bool):
        raise ContractError(400, "spark_invalid_request", "stream must be boolean")
    if (
        not isinstance(clean["temperature"], (int, float))
        or isinstance(clean["temperature"], bool)
        or not 0 <= clean["temperature"] <= 2
    ):
        raise ContractError(400, "spark_invalid_request", "temperature must be between 0 and 2")
    if not isinstance(clean["top_p"], (int, float)) or isinstance(clean["top_p"], bool) or not 0 < clean["top_p"] <= 1:
        raise ContractError(400, "spark_invalid_request", "top_p must be greater than 0 and at most 1")
    if "stop" in payload:
        stop = payload["stop"]
        if not isinstance(stop, str) and not (isinstance(stop, list) and all(isinstance(item, str) for item in stop)):
            raise ContractError(400, "spark_invalid_request", "stop must be a string or array of strings")
        clean["stop"] = stop
    return clean, estimate


def is_retryable(status: int | None = None, *, connection_error: bool = False, response_started: bool = False) -> bool:
    return not response_started and (connection_error or status in RETRYABLE_STATUS)


def verify_remote_models(document: Any, configured_model: str) -> None:
    ids = (
        [item.get("id") for item in document.get("data", []) if isinstance(item, dict)]
        if isinstance(document, dict)
        else []
    )
    if configured_model not in ids:
        raise ContractError(
            502,
            "spark_remote_model_mismatch",
            "remote /models does not advertise configured Spark model",
            configured_model=configured_model,
            remote_models=ids,
        )

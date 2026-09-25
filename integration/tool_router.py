"""Needle3 selection boundary: inference, validation, and policy, never execution."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator


class RouterError(ValueError):
    """Invalid router input or invalid model output."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any]
    effect: str = "read"
    allowed_roles: frozenset[str] = field(default_factory=lambda: frozenset({"agent"}))
    requires_confirmation: bool = False

    def needle_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class RouterSettings:
    confidence_threshold: float = 0.70
    confirm_threshold: float = 0.30
    max_candidates: int = 5
    max_new_tokens: int = 512
    allowed_effects: frozenset[str] = field(default_factory=lambda: frozenset({"read"}))

    @classmethod
    def from_env(cls) -> "RouterSettings":
        settings = cls(
            confidence_threshold=float(os.getenv("NEEDLE_CONFIDENCE_THRESHOLD", "0.70")),
            confirm_threshold=float(os.getenv("NEEDLE_CONFIRM_THRESHOLD", "0.30")),
            max_candidates=int(os.getenv("NEEDLE_MAX_CANDIDATES", "5")),
            max_new_tokens=int(os.getenv("NEEDLE_MAX_NEW_TOKENS", "512")),
            allowed_effects=frozenset(
                item.strip() for item in os.getenv("NEEDLE_ALLOWED_EFFECTS", "read").split(",") if item.strip()
            ),
        )
        if not 0 <= settings.confirm_threshold <= settings.confidence_threshold <= 1:
            raise RouterError("Needle confidence thresholds must satisfy 0 <= confirm <= act <= 1")
        if not 1 <= settings.max_candidates <= 5:
            raise RouterError("NEEDLE_MAX_CANDIDATES must be between 1 and 5")
        if settings.max_new_tokens <= 0:
            raise RouterError("NEEDLE_MAX_NEW_TOKENS must be positive")
        if not settings.allowed_effects:
            raise RouterError("NEEDLE_ALLOWED_EFFECTS cannot be empty")
        return settings


class SelectionBackend(Protocol):
    def select(self, intent: str, tools: Sequence[Mapping[str, Any]], max_new_tokens: int) -> Mapping[str, Any]: ...


class NeedleBackend:
    """Uses Needle.complete(), deliberately never Needle.run()."""

    def __init__(self, *, custom_weights: str | None = None):
        self.custom_weights = custom_weights or os.getenv("NEEDLE_CUSTOM_WEIGHTS") or None
        self.loaded = False
        self.last_route_ms: float | None = None
        self.last_response: Mapping[str, Any] | None = None
        self._lock = threading.Lock()

    def select(self, intent: str, tools: Sequence[Mapping[str, Any]], max_new_tokens: int) -> Mapping[str, Any]:
        import needle

        kwargs: dict[str, Any] = {
            "tools": list(tools),
            "generation": 3,
            "auto_date": False,
        }
        # Omitting weights is significant: stock Needle3 then uses the native
        # in-process base archive, loaded once per process. Explicit weights
        # select upstream's tuned-model worker subprocess.
        if self.custom_weights:
            kwargs["weights"] = self.custom_weights
        with self._lock:
            started = time.perf_counter()
            agent = needle.Needle(**kwargs)
            try:
                response = agent.complete(intent, max_new_tokens=max_new_tokens)
                self.last_response = response
                self.loaded = True
                return response
            finally:
                self.last_route_ms = (time.perf_counter() - started) * 1000
                agent.close()

    def status(self) -> dict[str, Any]:
        from integration.needle_runtime import runtime_status

        return {
            **runtime_status(loaded=self.loaded, last_route_ms=self.last_route_ms),
            "custom_weights": bool(self.custom_weights),
        }


@dataclass(frozen=True)
class ToolDecision:
    status: str
    tool: str | None
    arguments: Mapping[str, Any]
    confidence: float | None
    reason: str
    model_reasoning: str | None = None
    model_validation: Mapping[str, Any] = field(default_factory=dict)


class ToolRouter:
    """Select and validate one candidate tool without executing it."""

    def __init__(self, backend: SelectionBackend, settings: RouterSettings | None = None):
        self.backend = backend
        self.settings = settings or RouterSettings.from_env()

    def route(self, intent: str, candidates: Sequence[ToolSpec], *, role: str = "agent") -> ToolDecision:
        if not isinstance(intent, str) or not intent.strip():
            raise RouterError("intent must be a non-empty string")
        if not candidates:
            return ToolDecision("abstain", None, {}, None, "no candidate tools")
        if len(candidates) > self.settings.max_candidates:
            raise RouterError(f"candidate set has {len(candidates)} tools; maximum is {self.settings.max_candidates}")
        by_name: dict[str, ToolSpec] = {}
        for tool in candidates:
            if tool.name in by_name:
                raise RouterError(f"duplicate candidate tool: {tool.name}")
            Draft202012Validator.check_schema(tool.parameters)
            by_name[tool.name] = tool

        raw = self.backend.select(
            intent.strip(), [tool.needle_schema() for tool in candidates], self.settings.max_new_tokens
        )
        if not isinstance(raw, Mapping):
            raise RouterError("Needle response must be an object")
        if raw.get("success") is False:
            return ToolDecision(
                "rejected",
                None,
                {},
                None,
                f"Needle inference failed: {raw.get('error') or 'unknown error'}",
            )
        calls = raw.get("function_calls") or []
        suppressed = raw.get("suppressed_calls") or []
        forced_confirmation = False
        confidence = raw.get("confidence")
        reasoning = raw.get("reasoning") if isinstance(raw.get("reasoning"), str) else None
        validation = raw.get("validation") or {}
        if not isinstance(validation, Mapping):
            raise RouterError("Needle validation must be an object")
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1
        ):
            raise RouterError("Needle confidence must be null or between 0 and 1")
        if not isinstance(calls, list):
            raise RouterError("Needle function_calls must be an array")
        if not isinstance(suppressed, list):
            raise RouterError("Needle suppressed_calls must be an array")
        if not calls and suppressed:
            calls = suppressed
            forced_confirmation = True
        if not calls:
            return ToolDecision("abstain", None, {}, confidence, "Needle selected no tool", reasoning)
        if raw.get("type") != "call":
            raise RouterError("Needle returned function calls with a non-call response type")
        if len(calls) != 1:
            return ToolDecision("confirm", None, {}, confidence, "multiple tool calls require decomposition", reasoning)

        call = calls[0]
        if not isinstance(call, Mapping) or not isinstance(call.get("name"), str):
            raise RouterError("Needle call must contain a tool name")
        name = call["name"]
        tool = by_name.get(name)
        if tool is None:
            raise RouterError(f"Needle selected a tool outside the candidate set: {name}")
        arguments = call.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise RouterError("Needle tool arguments must be an object")
        errors = sorted(
            Draft202012Validator(tool.parameters).iter_errors(arguments), key=lambda error: list(error.path)
        )
        if errors:
            detail = "; ".join(error.message for error in errors)
            return ToolDecision(
                "rejected", name, dict(arguments), confidence, f"schema validation failed: {detail}", reasoning
            )
        if role not in tool.allowed_roles:
            return ToolDecision(
                "rejected", name, dict(arguments), confidence, f"role {role!r} is not allowed", reasoning
            )
        if tool.effect not in self.settings.allowed_effects:
            return ToolDecision(
                "rejected", name, dict(arguments), confidence, f"effect {tool.effect!r} is not allowed", reasoning
            )
        if validation.get("ungrounded") or validation.get("negation"):
            return ToolDecision(
                "rejected",
                name,
                dict(arguments),
                confidence,
                "Needle marked the proposal ungrounded or negated",
                reasoning,
                dict(validation),
            )
        if forced_confirmation:
            return ToolDecision("confirm", name, dict(arguments), confidence, "Needle suppressed the call", reasoning)
        if confidence is None or confidence < self.settings.confirm_threshold:
            return ToolDecision(
                "abstain", name, dict(arguments), confidence, "confidence below confirmation threshold", reasoning
            )
        if confidence < self.settings.confidence_threshold or tool.requires_confirmation:
            return ToolDecision("confirm", name, dict(arguments), confidence, "confirmation required", reasoning)
        return ToolDecision("selected", name, dict(arguments), confidence, "schema and policy accepted", reasoning)


class ToolGateway:
    """Deterministic executor; accepts only a policy-approved router decision."""

    def __init__(self, handlers: Mapping[str, Callable[..., Any]]):
        self._handlers = dict(handlers)

    def execute(self, decision: ToolDecision) -> Any:
        if decision.status != "selected" or decision.tool is None:
            raise PermissionError(f"tool decision is not executable: {decision.status}")
        handler = self._handlers.get(decision.tool)
        if handler is None:
            raise KeyError(f"no deterministic handler registered for {decision.tool}")
        return handler(**dict(decision.arguments))

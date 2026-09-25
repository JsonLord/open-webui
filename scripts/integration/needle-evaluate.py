#!/usr/bin/env python3
"""Run the checked-in routing evaluation against real offline Needle3."""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from integration.needle_runtime import NeedleArtifactContract
from integration.tool_router import NeedleBackend, ToolRouter, ToolSpec

TOOLS = {
    "read_file": ToolSpec(
        "read_file",
        "Read one repository file.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    "list_directory": ToolSpec(
        "list_directory",
        "List one repository directory.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    "git_status": ToolSpec(
        "git_status",
        "Read Git working-tree status.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "git_log": ToolSpec(
        "git_log",
        "Read recent Git commit history.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "run_validation": ToolSpec(
        "run_validation",
        "Run an approved validation family.",
        {
            "type": "object",
            "properties": {"command": {"type": "string", "enum": ["unit", "lint"]}},
            "required": ["command"],
            "additionalProperties": False,
        },
    ),
    "find_symbol": ToolSpec(
        "find_symbol",
        "Find a named code symbol.",
        {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    ),
}


def mean(values):
    return statistics.mean(values) if values else None


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def main() -> int:
    NeedleArtifactContract.from_env().verify()
    cases = json.loads((ROOT / "integration" / "needle-eval-cases.json").read_text())
    backend = NeedleBackend()
    router = ToolRouter(backend)
    tool_correct = argument_correct = schema_valid = 0
    expected_abstain = predicted_abstain = true_abstain = 0
    correct_confidence = []
    incorrect_confidence = []
    latencies = []
    outcomes = []
    for case in cases:
        candidates = [TOOLS[name] for name in case["candidates"]]
        started = time.perf_counter()
        decision = router.route(case["intent"], candidates)
        latencies.append((time.perf_counter() - started) * 1000)
        expected_tool = case["expected_tool"]
        tool_match = decision.tool == expected_tool
        args_match = tool_match and dict(decision.arguments) == case["expected_arguments"]
        tool_correct += tool_match
        argument_correct += args_match
        expected_abstain += expected_tool is None
        predicted_abstain += decision.status == "abstain"
        true_abstain += expected_tool is None and decision.status == "abstain"
        raw = backend.last_response or {}
        calls = raw.get("function_calls") or raw.get("suppressed_calls") or []
        valid = True
        for call in calls:
            tool = TOOLS.get(call.get("name"))
            if tool is None or list(Draft202012Validator(tool.parameters).iter_errors(call.get("arguments", {}))):
                valid = False
        schema_valid += valid
        confidence = decision.confidence
        if confidence is not None:
            (correct_confidence if tool_match and args_match else incorrect_confidence).append(confidence)
        outcomes.append(
            {
                "intent": case["intent"],
                "status": decision.status,
                "tool": decision.tool,
                "confidence": confidence,
                "tool_match": tool_match,
                "arguments_match": args_match,
            }
        )
    precision = true_abstain / predicted_abstain if predicted_abstain else None
    recall = true_abstain / expected_abstain if expected_abstain else None
    report = {
        "cases": len(cases),
        "tool_exact_match": tool_correct / len(cases),
        "argument_exact_match": argument_correct / len(cases),
        "schema_valid_rate": schema_valid / len(cases),
        "abstention_precision": precision,
        "abstention_recall": recall,
        "mean_confidence_correct": mean(correct_confidence),
        "mean_confidence_incorrect": mean(incorrect_confidence),
        "p50_latency_ms": statistics.median(latencies),
        "p95_latency_ms": percentile(latencies, 0.95),
        "thresholds_changed": False,
        "outcomes": outcomes,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)

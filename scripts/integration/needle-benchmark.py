#!/usr/bin/env python3
"""Measure real stock Needle3 cold/warm routing without executing tools."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integration.needle_runtime import NeedleArtifactContract

TOOLS = [
    {
        "name": "read_file",
        "description": "Read a repository file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "git_status",
        "description": "Read Git working tree status.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "run_validation",
        "description": "Run an approved validation command.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "enum": ["unit", "lint"]}},
            "required": ["command"],
        },
    },
    {
        "name": "find_symbol",
        "description": "Find a code symbol.",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
    },
    {
        "name": "list_directory",
        "description": "List a repository directory.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
]
QUERIES = (
    "Read integration/spark_contract.py",
    "Show the Git working tree status",
    "Run the unit validation",
    "Send an email to the maintainer",
)


def rss_bytes() -> int:
    fields = Path("/proc/self/statm").read_text().split()
    return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()
    if args.iterations < 5:
        parser.error("--iterations must be at least 5")
    os.environ["HF_HUB_OFFLINE"] = "1"
    contract = NeedleArtifactContract.from_env()
    artifacts = contract.verify()
    import needle

    rss_before = rss_bytes()
    results = {}
    process_started = time.perf_counter()
    cpu_started = time.process_time()
    cold_init_ms = None
    cold_first_inference_ms = None
    for count in (1, 3, 5):
        tools = TOOLS[:count]
        init_samples = []
        generation_samples = []
        for index in range(args.iterations):
            started = time.perf_counter()
            agent = needle.Needle(tools=tools, generation=3, auto_date=False)
            initialized = time.perf_counter()
            agent.complete(QUERIES[index % len(QUERIES)], max_new_tokens=512)
            completed = time.perf_counter()
            agent.close()
            init_samples.append((initialized - started) * 1000)
            generation_samples.append((completed - initialized) * 1000)
            if count == 1 and index == 0:
                cold_init_ms = init_samples[-1]
                cold_first_inference_ms = generation_samples[-1]
        warm_init = init_samples[1:] if count == 1 else init_samples
        warm_generation = generation_samples[1:] if count == 1 else generation_samples
        results[str(count)] = {
            "schema_init_p50_ms": statistics.median(warm_init),
            "schema_init_p95_ms": percentile(warm_init, 0.95),
            "route_p50_ms": statistics.median(warm_generation),
            "route_p95_ms": percentile(warm_generation, 0.95),
            "route_p99_ms": percentile(warm_generation, 0.99),
        }
    rss_after = rss_bytes()
    print(
        json.dumps(
            {
                "benchmark_wall_ms": (time.perf_counter() - process_started) * 1000,
                "benchmark_cpu_ms": (time.process_time() - cpu_started) * 1000,
                "cold_initialization_ms": cold_init_ms,
                "cold_first_inference_ms": cold_first_inference_ms,
                "rss_before_bytes": rss_before,
                "rss_after_bytes": rss_after,
                "rss_delta_bytes": rss_after - rss_before,
                **artifacts,
                "candidate_counts": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3
"""Evaluate bounded GraphContext relevance against an existing local index."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.control_plane.graph import RepositoryGraphService, RepositoryIdentity  # noqa: E402
from open_webui.control_plane.graph_context import (  # noqa: E402
    GraphContextEnricher,
    GraphContextRequest,
)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--graph-root', required=True, type=Path)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--cases', required=True, type=Path)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    graphs = RepositoryGraphService(args.graph_root)
    identity = graphs.identity(RepositoryIdentity(args.repository), args.revision)
    enricher = GraphContextEnricher(graphs)
    rows = []
    for case in cases:
        started = time.monotonic()
        outcome = enricher.enrich(
            GraphContextRequest(
                args.repository,
                args.revision,
                case['objective'],
                tuple(case.get('acceptance_criteria', [])),
                tuple(case.get('explicit_paths', [])),
                tuple(case.get('explicit_symbols', [])),
            ),
            index_id=identity.index_id,
            graph_state='READY',
        )
        if not outcome.briefing:
            rows.append({'id': case['id'], 'status': outcome.status, 'reason': outcome.reason})
            continue
        context = outcome.briefing.context
        files = set(context.relevant_files)
        symbols = {node.name.removesuffix('()') for node in context.nodes}
        expected_files = set(case.get('expected_files', []))
        expected_symbols = set(case.get('expected_symbols', []))
        rows.append(
            {
                'id': case['id'],
                'status': 'ready',
                'relevant_file_recall': len(files & expected_files) / max(1, len(expected_files)),
                'relevant_symbol_recall': len(symbols & expected_symbols) / max(1, len(expected_symbols)),
                'irrelevant_files': len(files - expected_files),
                'bytes': outcome.briefing.byte_count,
                'estimated_tokens': outcome.briefing.estimated_tokens,
                'query_count': context.query_count,
                'latency_ms': round((time.monotonic() - started) * 1000, 3),
            }
        )
    ready = [row for row in rows if row['status'] == 'ready']
    latencies = [row['latency_ms'] for row in ready]
    summary = {
        'cases': rows,
        'ready_cases': len(ready),
        'average_relevant_file_recall': statistics.mean(row['relevant_file_recall'] for row in ready) if ready else 0,
        'average_relevant_symbol_recall': statistics.mean(row['relevant_symbol_recall'] for row in ready)
        if ready
        else 0,
        'average_irrelevant_files': statistics.mean(row['irrelevant_files'] for row in ready) if ready else 0,
        'average_bytes': statistics.mean(row['bytes'] for row in ready) if ready else 0,
        'average_estimated_tokens': statistics.mean(row['estimated_tokens'] for row in ready) if ready else 0,
        'p50_latency_ms': percentile(latencies, 0.5) if latencies else 0,
        'p95_latency_ms': percentile(latencies, 0.95) if latencies else 0,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

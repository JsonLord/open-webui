#!/usr/bin/env python3
"""Evaluate JIT policy classification, schema validity, and fallback rate."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.control_plane.adapters import JITPlanner  # noqa: E402
from open_webui.control_plane.domain import JITTaskContext  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', required=True, type=Path)
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    planner = JITPlanner()

    results = []
    fallback_count = 0
    valid_schema_count = 0

    for case in cases:
        context = JITTaskContext(
            task_id=case.get('id', 'test-task'),
            repository=case.get('repository', 'open-webui/open-webui'),
            base_revision=case.get('revision', 'main'),
            objective=case['objective'],
            explicit_paths=tuple(case.get('explicit_paths', [])),
            explicit_symbols=tuple(case.get('explicit_symbols', [])),
        )

        decision = planner.plan_initial(context)
        if decision.decision_id.startswith('fallback-'):
            fallback_count += 1
        else:
            valid_schema_count += 1

        results.append({
            'id': case['id'],
            'decision_id': decision.decision_id,
            'task_class': decision.task_class.value,
            'autonomy': decision.execution_policy.autonomy.value,
            'max_replans': decision.execution_policy.max_replans,
            'strategy_bytes': len(decision.plan_seed.encode('utf-8')),
        })

    summary = {
        'total_cases': len(cases),
        'valid_schema_count': valid_schema_count,
        'fallback_count': fallback_count,
        'fallback_rate': fallback_count / max(1, len(cases)),
        'average_strategy_bytes': statistics.mean(r['strategy_bytes'] for r in results) if results else 0,
        'results': results,
    }

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

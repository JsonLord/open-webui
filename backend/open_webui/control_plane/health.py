from __future__ import annotations

import os
import shutil


def _needle_status():
    try:
        from integration.needle_runtime import runtime_status

        return runtime_status()
    except ImportError:
        return {'loaded': False, 'runtime_verified': False}


def aggregate_health(probes: dict[str, dict] | None = None) -> dict:
    needle = _needle_status()
    state = {
        'postgresql': {'configured': bool(os.getenv('DATABASE_URL')), 'reachable': False, 'runtime_verified': False},
        'plandex': {'configured': bool(shutil.which('plandex')), 'reachable': False, 'runtime_verified': False},
        'headroom': {'configured': bool(os.getenv('HEADROOM_BASE_URL')), 'reachable': False, 'runtime_verified': False},
        'spark_contract': {
            'configured': bool(os.getenv('SPARK_BASE_URL')),
            'reachable': False,
            'runtime_verified': False,
        },
        'needle': {
            'configured': True,
            'reachable': needle['loaded'],
            'runtime_verified': needle['runtime_verified'],
            'artifacts_present': needle.get('artifacts_present', False),
        },
        'github_mcp': {
            'configured': bool(shutil.which('github-mcp-server')),
            'reachable': False,
            'runtime_verified': False,
            'authenticated': bool(os.getenv('GITHUB_PAT')),
        },
        'graphify': {
            'configured': bool(shutil.which(os.getenv('GRAPHIFY_EXECUTABLE', 'graphify'))),
            'reachable': bool(shutil.which(os.getenv('GRAPHIFY_EXECUTABLE', 'graphify'))),
            'runtime_verified': False,
            'storage_available': False,
            'indexing_capable': bool(shutil.which(os.getenv('GRAPHIFY_EXECUTABLE', 'graphify'))),
        },
        'git': {
            'configured': bool(shutil.which('git')),
            'reachable': bool(shutil.which('git')),
            'runtime_verified': bool(shutil.which('git')),
        },
        'gh': {
            'configured': bool(shutil.which('gh')),
            'reachable': bool(shutil.which('gh')),
            'runtime_verified': False,
        },
    }
    for key, value in (probes or {}).items():
        state.setdefault(key, {}).update(value)
    # Graphify is optional supporting intelligence in Phase 4 and therefore
    # does not make the application unhealthy when unavailable.
    required = {'postgresql', 'plandex', 'headroom', 'spark_contract', 'needle', 'github_mcp', 'git'}
    return {
        'ok': all(state[name]['configured'] and state[name]['reachable'] for name in required),
        'components': state,
    }

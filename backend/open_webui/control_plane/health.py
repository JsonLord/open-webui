from __future__ import annotations

import os
import shutil


def _needle_status():
    try:
        from integration.needle_runtime import runtime_status

        return runtime_status()
    except ImportError:
        return {'loaded': False, 'runtime_verified': False}


ARTIFACT_MANIFEST = {
    'plandex_source_revision': os.getenv('PLANDEX_REVISION', 'unknown'),
    'github_mcp_version': os.getenv('GITHUB_MCP_VERSION', 'stdio-2025-06-18'),
    'graphify_version': '0.9.67',
    'needle_version': '0.1.0',
    'tokenizer_cache_key': 'o200k_base',
    'headroom_version': '0.1.0',
}

PERSISTENCE_MAP = {
    'must_persist': [
        '/var/lib/postgresql/data',
        '~/.plandex',
        'backend/open_webui/data',
        '/tmp/graphify-indexes',
    ],
    'may_rebuild': [
        '/tmp/graphify-staging',
        '~/.cache/tiktoken',
    ],
    'must_not_persist': [
        'logs/secrets',
        'temp_env_dumps',
    ],
}


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
        'jit': {
            'configured': bool(os.getenv('JIT_BASE_URL')),
            'reachable': False,
            'runtime_verified': False,
            'degraded': True,
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

    critical_local = {'postgresql', 'plandex', 'git'}
    optional_or_remote = {'graphify', 'jit', 'headroom', 'spark_contract', 'needle', 'github_mcp', 'gh'}

    critical_ok = all(state[name]['configured'] and state[name]['reachable'] for name in critical_local if name in state)

    return {
        'ok': critical_ok,
        'critical_local_ok': critical_ok,
        'components': state,
        'artifact_manifest': ARTIFACT_MANIFEST,
        'persistence_map': PERSISTENCE_MAP,
    }

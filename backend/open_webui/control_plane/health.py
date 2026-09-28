from __future__ import annotations

import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path


APP_PERSIST_ROOT = Path(os.getenv('APP_PERSIST_ROOT', '/data/agent-platform'))


def _needle_status():
    try:
        from integration.needle_runtime import runtime_status

        return runtime_status()
    except ImportError:
        return {'loaded': False, 'runtime_verified': False}


def warmup_litellm_worker(base_url: str = 'http://127.0.0.1:8787', timeout: int = 15) -> dict:
    """Proactively warm LiteLLM / Headroom worker during startup to prevent cold-start timeout."""
    try:
        req = urllib.request.Request(f'{base_url.rstrip("/")}/health', headers={'User-Agent': 'control-plane-preflight'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {'warmed': True, 'status_code': resp.status}
    except Exception as exc:
        return {'warmed': False, 'error': str(exc)}


def check_storage_precheck() -> dict:
    require_persistent = os.getenv('REQUIRE_PERSISTENT_STORAGE', '0') == '1'
    root_exists = APP_PERSIST_ROOT.exists()
    root_writable = False

    if root_exists:
        try:
            test_file = APP_PERSIST_ROOT / '.write_test'
            test_file.touch()
            test_file.unlink()
            root_writable = True
        except Exception:
            root_writable = False

    ok = root_writable if require_persistent else True
    return {
        'ok': ok,
        'persistent_expected': require_persistent,
        'root_path': str(APP_PERSIST_ROOT),
        'exists': root_exists,
        'writable': root_writable,
        'mode': 'persistent' if root_writable else ('ephemeral' if not require_persistent else 'unavailable'),
    }


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
        f'{APP_PERSIST_ROOT}/pgdata',
        f'{APP_PERSIST_ROOT}/plandex/server',
        f'{APP_PERSIST_ROOT}/plandex/cli',
        f'{APP_PERSIST_ROOT}/open-webui',
        f'{APP_PERSIST_ROOT}/control-plane',
        f'{APP_PERSIST_ROOT}/repositories',
        f'{APP_PERSIST_ROOT}/graphify',
        f'{APP_PERSIST_ROOT}/runtime-state',
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
    storage = check_storage_precheck()
    spark_configured = bool(os.getenv('SPARK_API_KEY') and os.getenv('SPARK_BASE_URL'))
    litellm_warmup = warmup_litellm_worker() if spark_configured else {'warmed': False}

    state = {
        'storage': storage,
        'postgresql': {'configured': bool(os.getenv('DATABASE_URL')), 'reachable': False, 'runtime_verified': False},
        'plandex': {'configured': bool(shutil.which('plandex')), 'reachable': False, 'runtime_verified': False},
        'headroom': {
            'configured': bool(os.getenv('HEADROOM_BASE_URL')),
            'reachable': False,
            'runtime_verified': False,
            'litellm_warmed': litellm_warmup['warmed'],
        },
        'spark_contract': {
            'configured': spark_configured,
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

    critical = {
        'storage': 'ready' if storage['ok'] else 'failed',
        'postgres': 'ready' if state['postgresql']['reachable'] else 'failed',
        'plandex': 'ready' if state['plandex']['reachable'] else 'failed',
        'tokenizer': 'ready' if state.get('tokenizer', {}).get('runtime_verified') else 'failed',
        'plandex_auth': 'ready' if state.get('plandex_auth', {}).get('runtime_verified') else 'failed',
        'open_webui': 'ready' if state.get('open_webui', {}).get('reachable') else 'failed',
    }
    critical_ok = all(value == 'ready' for value in critical.values())

    def optional_state(name: str) -> str:
        component = state[name]
        if not component.get('configured'):
            return 'not_configured'
        if component.get('runtime_verified') or component.get('reachable'):
            return 'ready'
        return 'unavailable'

    degraded = {
        'graphify': optional_state('graphify'),
        'needle': optional_state('needle'),
        'spark': optional_state('spark_contract'),
        'jit': optional_state('jit'),
        'github_authenticated': (
            'ready' if state['github_mcp']['authenticated'] else 'not_configured'
        ),
    }

    return {
        'ok': critical_ok,
        'deployment_ready': critical_ok,
        'critical_local_ok': critical_ok,
        'critical': critical,
        'degraded': degraded,
        'components': state,
        'artifact_manifest': ARTIFACT_MANIFEST,
        'persistence_map': PERSISTENCE_MAP,
    }

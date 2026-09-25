#!/usr/bin/env python3
"""Provision and validate native Plandex local auth without exposing secrets."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.control_plane.plandex_tokenizer import preflight as tokenizer_preflight  # noqa: E402


def child_environment() -> tuple[dict[str, str], Path, str]:
    cli_home = Path(os.getenv('PLANDEX_CLI_HOME', '/var/lib/plandex-cli')).resolve()
    host = os.getenv('PLANDEX_API_HOST', 'http://127.0.0.1:8099').rstrip('/')
    env = dict(os.environ)
    env.update({'HOME': str(cli_home), 'PLANDEX_ENV': 'development', 'PLANDEX_API_HOST': host})
    for key in ('GITHUB_PAT', 'GITHUB_PERSONAL_ACCESS_TOKEN', 'GH_TOKEN'):
        env.pop(key, None)
    return env, cli_home, host


def run_native(args: list[str], env: dict[str, str]) -> bool:
    executable = os.getenv('PLANDEX_CLI', 'plandex')
    result = subprocess.run(
        [executable, *args],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    return result.returncode == 0


def server_reachable(host: str) -> bool:
    try:
        with urllib.request.urlopen(f'{host}/health', timeout=5) as response:
            return response.status == 200
    except OSError:
        return False


def main() -> int:
    env, cli_home, host = child_environment()
    status = {
        'server_reachable': server_reachable(host),
        'local_mode': False,
        'cli_home_present': cli_home.is_dir(),
        'auth_present': False,
        'auth_valid': False,
    }
    if not status['server_reachable']:
        print(json.dumps(status, sort_keys=True))
        return 1

    try:
        tokenizer_preflight(env.get('TIKTOKEN_CACHE_DIR'))
        cli_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        cli_home.chmod(0o700)
        native_home = cli_home / '.plandex-home-dev-v2'
        auth_file = native_home / 'auth.json'
        accounts_file = native_home / 'accounts.json'
        status['cli_home_present'] = True
        status['auth_present'] = auth_file.is_file() and accounts_file.is_file()

        # Native validation reuses valid auth without creating a session. Only
        # invoke the native local bootstrap when validation fails.
        if status['auth_present']:
            status['auth_valid'] = run_native(['sign-in', '--local-host', host, '--validate-only'], env)
        if not status['auth_valid']:
            if not run_native(['sign-in', '--local-host', host], env):
                print(json.dumps(status, sort_keys=True))
                return 1
            status['auth_valid'] = run_native(['sign-in', '--local-host', host, '--validate-only'], env)
        status['auth_present'] = auth_file.is_file() and accounts_file.is_file()
        if status['auth_present']:
            auth_file.chmod(0o600)
            accounts_file.chmod(0o600)
            native_home.chmod(0o700)
        status['local_mode'] = status['auth_valid']
        print(json.dumps(status, sort_keys=True))
        return 0 if status['auth_valid'] else 1
    except (OSError, RuntimeError, subprocess.SubprocessError):
        print(json.dumps(status, sort_keys=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

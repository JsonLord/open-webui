from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

from .plandex_tokenizer import TokenizerArtifactError
from .plandex_tokenizer import preflight as tokenizer_preflight


def redact(text: str, secrets: tuple[str, ...] | None = None) -> str:
    for secret in secrets or (os.getenv('GITHUB_PAT', ''),):
        if secret:
            text = text.replace(secret, '[REDACTED]')
    return text


@dataclass(frozen=True)
class TaskInput:
    title: str
    objective: str
    acceptance_criteria: tuple[str, ...] = ()
    issue_number: int | None = None
    issue_url: str | None = None
    labels: tuple[str, ...] = ()
    relevant_comments: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlandexPlanIdentity:
    plan_id: str
    plan_name: str
    project_id: str
    branch: str


@dataclass(frozen=True)
class PlandexContextLoadResult:
    status: str
    name: str
    native_id: str | None = None


def normalize_issue(payload: dict[str, Any], *, max_body_chars: int = 12000, max_comments: int = 10) -> TaskInput:
    number = payload.get('number')
    if not isinstance(number, int) or number <= 0:
        raise ValueError('invalid GitHub issue number')
    title, body, url = (
        str(payload.get('title', ''))[:500],
        str(payload.get('body') or '')[:max_body_chars],
        str(payload.get('html_url', ''))[:1000],
    )
    labels = tuple(str(x.get('name', ''))[:100] for x in payload.get('labels', []) if isinstance(x, dict))
    comments = tuple(
        str(x.get('body', ''))[:2000] for x in payload.get('comments_data', [])[:max_comments] if isinstance(x, dict)
    )
    return TaskInput(title, body, (), number, url, labels, comments)


class GitHubMCP:
    """Minimal stdio MCP client facade; raw execution is never exposed over HTTP."""

    TOOLSETS = 'repos,issues,pull_requests,actions'

    def __init__(self, command=('github-mcp-server', 'stdio', '--read-only'), timeout=30):
        self.command, self.process, self.lock, self.next_id = command, None, threading.Lock(), 1
        self.timeout = timeout

    def start(self):
        token = os.getenv('GITHUB_PAT', '')
        if not token:
            raise RuntimeError('github_auth_failed')
        env = {
            **os.environ,
            'GITHUB_PERSONAL_ACCESS_TOKEN': token,
            'GH_TOKEN': token,
            'GITHUB_TOOLSETS': self.TOOLSETS,
            'GITHUB_READ_ONLY': '1',
        }
        self.process = subprocess.Popen(
            self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
        )
        self._request(
            'initialize',
            {
                'protocolVersion': '2025-06-18',
                'capabilities': {},
                'clientInfo': {'name': 'open-webui-control-plane', 'version': '0.1'},
            },
        )
        if self.process.stdin is None:
            raise RuntimeError('github_mcp_unavailable')
        self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
        self.process.stdin.flush()

    def close(self):
        if not self.process:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream:
                stream.close()

    def _request(self, method: str, params: dict) -> dict:
        if not self.process or self.process.poll() is not None:
            raise RuntimeError('github_mcp_unavailable')
        with self.lock:
            if self.process.stdin is None:
                raise RuntimeError('github_mcp_unavailable')
            request_id = self.next_id
            self.next_id += 1
            message = {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}
            self.process.stdin.write(json.dumps(message) + '\n')
            self.process.stdin.flush()
            response = self._read_response(request_id)
        if response.get('id') != request_id or 'error' in response:
            raise RuntimeError('github_mcp_request_failed')
        return response['result']

    def _read_response(self, request_id: int) -> dict:
        """Read newline-delimited MCP responses without waiting forever.

        Notifications may be interleaved with the response, so they are ignored
        until the matching request ID arrives.
        """
        deadline = time.monotonic() + self.timeout
        process = self.process
        if process is None or process.stdout is None:
            raise RuntimeError('github_mcp_unavailable')
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('github_mcp_unavailable')
            # stdout is a pipe. A small daemon reader would complicate lifecycle;
            # select keeps this synchronous adapter bounded on Linux/HF.
            wait_seconds = max(0.0, min(0.25, deadline - time.monotonic()))
            readable, _, _ = select.select([process.stdout], [], [], wait_seconds)
            if not readable:
                continue
            line = process.stdout.readline()
            if not line:
                raise RuntimeError('github_mcp_unavailable')
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError('github_mcp_protocol_error') from exc
            if response.get('id') == request_id:
                return response
        raise RuntimeError('github_mcp_timeout')

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._request('tools/call', {'name': name, 'arguments': arguments})

    def read_issue(self, repository: str, issue_number: int) -> dict[str, Any]:
        owner, repo = repository.split('/', 1)
        result = self.call_tool(
            'issue_read',
            {'method': 'get', 'owner': owner, 'repo': repo, 'issue_number': issue_number},
        )
        payload = result.get('structuredContent')
        if isinstance(payload, dict):
            return payload
        for item in result.get('content', []):
            if item.get('type') != 'text':
                continue
            try:
                payload = json.loads(item.get('text', ''))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise RuntimeError('github_mcp_invalid_response')


class PlandexExecutor:
    """Thin owner-scoped wrapper around the native Plandex CLI."""

    def __init__(self, executable='plandex', timeout=900, *, require_tokenizer=None):
        self.executable, self.timeout, self._processes = executable, timeout, {}
        self._lock = threading.Lock()
        self.require_tokenizer = require_tokenizer

    def _run(self, task_id, args, cwd, *, stdin_data: str | None = None):
        child_env = {
            key: value
            for key, value in os.environ.items()
            if key not in {'GITHUB_PAT', 'GITHUB_PERSONAL_ACCESS_TOKEN', 'GH_TOKEN'}
        }
        cli_home = child_env.get('PLANDEX_CLI_HOME')
        if cli_home:
            child_env['HOME'] = cli_home
            child_env.setdefault('PLANDEX_ENV', 'development')
            child_env.setdefault('PLANDEX_API_HOST', 'http://127.0.0.1:8099')
        # Production explicitly configures TIKTOKEN_CACHE_DIR. Validate it
        # before Go package initialization can panic or fall back to network.
        should_preflight = self.require_tokenizer
        if should_preflight is None:
            should_preflight = bool(child_env.get('TIKTOKEN_CACHE_DIR'))
        if should_preflight:
            try:
                tokenizer_preflight(child_env.get('TIKTOKEN_CACHE_DIR'))
            except TokenizerArtifactError as exc:
                raise RuntimeError('plandex_tokenizer_unavailable') from exc
        try:
            process = subprocess.Popen(
                [self.executable, *args],
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                start_new_session=True,
                env=child_env,
            )
        except OSError as exc:
            raise RuntimeError('plandex_unavailable') from exc
        with self._lock:
            self._processes[task_id] = process
        try:
            stdout, stderr = process.communicate(input=stdin_data, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self._terminate(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            raise RuntimeError('plandex_unavailable')
        finally:
            with self._lock:
                self._processes.pop(task_id, None)
        if process.returncode:
            raise RuntimeError('plandex_unavailable: ' + redact(stderr)[-1000:])
        return stdout.strip()

    def load_structural_context(self, task_id, cwd, context: str, context_identity: str):
        encoded = context.encode('utf-8')
        if not context or len(encoded) > int(os.getenv('GRAPH_CONTEXT_MAX_BYTES', '12288')):
            raise RuntimeError('plandex_context_invalid')
        if not context_identity or any(ch not in '0123456789abcdef' for ch in context_identity.lower()):
            raise RuntimeError('plandex_context_invalid')
        name = f'structural-context-{context_identity[:16].lower()}'

        def listed():
            try:
                payload = json.loads(self._run(task_id, ['ls', '--json'], cwd))
            except (json.JSONDecodeError, TypeError) as exc:
                raise RuntimeError('plandex_context_verification_failed') from exc
            if not isinstance(payload, list):
                raise RuntimeError('plandex_context_verification_failed')
            return [item for item in payload if isinstance(item, dict) and item.get('name') == name]

        existing = listed()
        if len(existing) == 1:
            return PlandexContextLoadResult('already_loaded', name, existing[0].get('id'))
        if len(existing) > 1:
            raise RuntimeError('plandex_context_verification_failed')
        self._run(task_id, ['load', '--name', name], cwd, stdin_data=context)
        loaded = listed()
        if len(loaded) != 1:
            raise RuntimeError('plandex_context_verification_failed')
        return PlandexContextLoadResult('loaded', name, loaded[0].get('id'))

    def create_plan(self, task_id, cwd):
        # `plandex new` prints a human summary, not the server plan UUID. Do not
        # mislabel stdout as an ID; the CLI makes the new plan current natively.
        name = f'agent-{task_id[:8]}'
        self._run(task_id, ['new', '--name', name, '--context-dir', '.'], cwd)
        payload = json.loads(self._run(task_id, ['current', '--json'], cwd))
        required = ('planId', 'planName', 'projectId', 'branch')
        if not all(isinstance(payload.get(key), str) and payload[key] for key in required):
            raise RuntimeError('plandex_plan_creation_failed')
        if payload['planName'] != name:
            raise RuntimeError('plandex_plan_creation_failed')
        return PlandexPlanIdentity(payload['planId'], payload['planName'], payload['projectId'], payload['branch'])

    def execute(self, task_id, cwd, objective):
        return self._run(task_id, ['tell', objective], cwd)

    def status(self, task_id, cwd):
        return self._run(task_id, ['current'], cwd)

    def pending_diff(self, task_id, cwd):
        return self._run(task_id, ['diff', '--plain'], cwd)

    def apply(self, task_id, cwd):
        return self._run(task_id, ['apply'], cwd)

    def cancel(self, task_id):
        with self._lock:
            process = self._processes.get(task_id)
        if process and process.poll() is None:
            self._terminate(process)
            return True
        return False

    @staticmethod
    def _terminate(process):
        """Terminate only the task-owned process group created by `_run`."""
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

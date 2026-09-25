from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import threading
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from open_webui.control_plane.adapters import GitHubMCP, PlandexExecutor
from open_webui.control_plane.git import GitWorktrees
from open_webui.control_plane.graph import RepositoryGraphService
from open_webui.control_plane.health import aggregate_health
from open_webui.control_plane.plandex_tokenizer import TokenizerArtifactError
from open_webui.control_plane.plandex_tokenizer import preflight as tokenizer_preflight
from open_webui.control_plane.repositories import RepositoryAllowlist
from open_webui.control_plane.service import ControlPlaneError, TaskService
from open_webui.control_plane.storage import PostgresTaskStore
from open_webui.internal.db import engine
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel, Field
from sqlalchemy import text

_service: TaskService | None = None
_task_lock = threading.Lock()


@asynccontextmanager
async def control_plane_lifespan(_app):
    # Production startup eagerly initializes persistence and applies
    # conservative recovery before serving task requests. SQLite development
    # installations keep the rest of Open WebUI usable; control-plane endpoints
    # return their explicit PostgreSQL-required 503.
    if engine.dialect.name == 'postgresql':
        get_service()
    yield


router = APIRouter(lifespan=control_plane_lifespan)


def get_service() -> TaskService:
    global _service
    if _service is None:
        if engine.dialect.name != 'postgresql':
            raise HTTPException(
                status_code=503,
                detail={'error': 'control_plane_requires_postgresql'},
            )
        store = PostgresTaskStore(engine)
        store.create_schema()
        store.recover_incomplete()
        root = Path(os.getenv('CONTROL_PLANE_DATA_DIR', './data/control-plane'))
        allowlist = RepositoryAllowlist.from_env()
        for repository in allowlist.repositories:
            store.register_repository(repository, {'source': 'ALLOWED_GITHUB_REPOS'})
        graphs = RepositoryGraphService(
            root / 'graphs',
            executable=os.getenv('GRAPHIFY_EXECUTABLE', 'graphify'),
            timeout=int(os.getenv('GRAPHIFY_INDEX_TIMEOUT', '300')),
        )
        _service = TaskService(
            store,
            GitWorktrees(root / 'repos', root / 'worktrees', allowlist),
            PlandexExecutor(),
            graphs,
        )
    return _service


class TaskCreate(BaseModel):
    repository: str
    issue_number: int | None = Field(default=None, gt=0)
    prompt: str | None = Field(default=None, min_length=1, max_length=12000)
    base_branch: str = 'main'


def public_task(task):
    """Serialize task state without leaking deployment filesystem layout."""
    payload = task.to_dict()
    payload.pop('worktree_path', None)
    payload.pop('graph_context_hash', None)
    payload.pop('graph_context_loaded_at', None)
    return payload


def api_error(exc: Exception):
    code = (
        exc.code
        if isinstance(exc, ControlPlaneError)
        else 'repository_not_allowed'
        if isinstance(exc, PermissionError)
        else 'request_rejected'
    )
    status = 404 if code == 'task_not_found' else 403 if code == 'repository_not_allowed' else 400
    raise HTTPException(status, detail={'error': code})


def health_state():
    database_reachable = False
    if engine.dialect.name == 'postgresql':
        try:
            with engine.connect() as connection:
                connection.execute(text('SELECT 1')).scalar_one()
            database_reachable = True
        except Exception:
            # Health is intentionally non-diagnostic: internal connection
            # details and stack traces never enter the public response.
            database_reachable = False
    plandex_host = os.getenv('PLANDEX_API_HOST', 'http://127.0.0.1:8099').rstrip('/')
    cli_home = Path(os.getenv('PLANDEX_CLI_HOME', '/var/lib/plandex-cli'))
    auth_present = all((cli_home / '.plandex-home-dev-v2' / name).is_file() for name in ('auth.json', 'accounts.json'))
    try:
        tokenizer_preflight(os.getenv('TIKTOKEN_CACHE_DIR'))
        tokenizer_verified = True
    except TokenizerArtifactError:
        tokenizer_verified = False
    try:
        with urllib.request.urlopen(f'{plandex_host}/health', timeout=1) as response:
            plandex_reachable = response.status == 200
    except OSError:
        plandex_reachable = False
    plandex_cli = shutil.which('plandex')
    auth_valid = False
    if plandex_cli and plandex_reachable and auth_present and tokenizer_verified:
        child_env = dict(os.environ)
        child_env.update({'HOME': str(cli_home), 'PLANDEX_ENV': 'development', 'PLANDEX_API_HOST': plandex_host})
        for key in ('GITHUB_PAT', 'GITHUB_PERSONAL_ACCESS_TOKEN', 'GH_TOKEN'):
            child_env.pop(key, None)
        try:
            auth_valid = (
                subprocess.run(
                    [plandex_cli, 'sign-in', '--local-host', plandex_host, '--validate-only'],
                    env=child_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                ).returncode
                == 0
            )
        except (OSError, subprocess.SubprocessError):
            auth_valid = False
    try:
        plan_runtime_verified = any(task.plandex_current_verified for task in get_service().store.list())
    except Exception:
        plan_runtime_verified = False
    graph_root = Path(os.getenv('CONTROL_PLANE_DATA_DIR', './data/control-plane')) / 'graphs'
    try:
        graph_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        graph_storage_available = os.access(graph_root, os.R_OK | os.W_OK | os.X_OK)
        graph_runtime_verified = any(task.graph_available for task in get_service().store.list())
    except OSError:
        graph_storage_available = False
        graph_runtime_verified = False
    return aggregate_health(
        {
            'postgresql': {
                'configured': engine.dialect.name == 'postgresql',
                'reachable': database_reachable,
                'runtime_verified': database_reachable,
            },
            'plandex': {
                'configured': bool(plandex_cli and os.getenv('PLANDEX_CLI_HOME')),
                'reachable': plandex_reachable,
                'runtime_verified': plandex_reachable and tokenizer_verified and auth_valid,
                'server_reachable': plandex_reachable,
                'tokenizer_verified': tokenizer_verified,
                'auth_present': auth_present,
                'auth_valid': auth_valid,
                'plan_runtime_verified': plan_runtime_verified,
            },
            'graphify': {
                'configured': bool(shutil.which(os.getenv('GRAPHIFY_EXECUTABLE', 'graphify'))),
                'reachable': bool(shutil.which(os.getenv('GRAPHIFY_EXECUTABLE', 'graphify'))),
                'runtime_verified': graph_runtime_verified,
                'storage_available': graph_storage_available,
                'indexing_capable': bool(
                    shutil.which(os.getenv('GRAPHIFY_EXECUTABLE', 'graphify')) and graph_storage_available
                ),
            },
        }
    )


def run_initial_task(task_id: str):
    """Run one queued task through issue loading, worktree, and native plan creation."""
    service = get_service()
    with _task_lock:
        task = service.require(task_id)
        if task.state.value != 'QUEUED':
            return
        mcp = None
        try:
            issue_loader = None
            if task.source == 'github_issue':
                mcp = GitHubMCP()
                mcp.start()
                issue_loader = mcp.read_issue
            service.prepare_and_create_plan(task_id, issue_loader=issue_loader)
        except PermissionError:
            service.fail(task_id, 'repository_not_allowed', 'Repository authorization failed')
        except ControlPlaneError as exc:
            if exc.code != 'task_cancelled':
                service.fail(task_id, exc.code, 'Initial task preparation failed')
        except RuntimeError as exc:
            code = str(exc).split(':', 1)[0]
            allowed = {
                'github_auth_failed',
                'github_mcp_unavailable',
                'github_mcp_request_failed',
                'github_mcp_timeout',
            }
            service.fail(
                task_id,
                code if code in allowed else 'repository_unavailable',
                'Initial task preparation failed',
            )
        finally:
            if mcp:
                mcp.close()


@router.get('/health')
def health(_=Depends(get_verified_user)):
    return health_state()


@router.get('/status')
def status(_=Depends(get_verified_user)):
    return health_state()


@router.post('/tasks')
def create_task(
    form: TaskCreate,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias='Idempotency-Key'),
    _=Depends(get_verified_user),
):
    try:
        task = get_service().create_task(**form.model_dump(), idempotency_key=idempotency_key)
        if task.state.value == 'QUEUED':
            background_tasks.add_task(run_initial_task, task.task_id)
        return public_task(task)
    except (ValueError, PermissionError, ControlPlaneError) as exc:
        api_error(exc)


@router.get('/tasks')
def list_tasks(_=Depends(get_verified_user)):
    service = get_service()
    return [public_task(service.refresh_graph_state(task.task_id)) for task in service.store.list()]


@router.get('/tasks/{task_id}')
def get_task(task_id: str, _=Depends(get_verified_user)):
    try:
        return public_task(get_service().refresh_graph_state(task_id))
    except ControlPlaneError as exc:
        api_error(exc)


@router.post('/tasks/{task_id}/cancel')
def cancel_task(task_id: str, _=Depends(get_verified_user)):
    try:
        return public_task(get_service().cancel(task_id))
    except (ValueError, ControlPlaneError) as exc:
        api_error(exc)


@router.get('/tasks/{task_id}/events')
def task_events(
    task_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    last_event_id: int | None = Header(default=None, alias='Last-Event-ID'),
    _=Depends(get_verified_user),
):
    try:
        get_service().require(task_id)
    except ControlPlaneError as exc:
        api_error(exc)

    async def stream():
        cursor = max(after, last_event_id or 0)
        while True:
            if await request.is_disconnected():
                return
            new_events = await anyio.to_thread.run_sync(get_service().store.events, task_id, cursor)
            for event in new_events:
                cursor = event.sequence
                yield f'id: {event.sequence}\nevent: {event.type}\ndata: {json.dumps(event.to_dict())}\n\n'
            task = await anyio.to_thread.run_sync(get_service().require, task_id)
            if task.state.value in {'COMPLETED', 'CANCELLED', 'FAILED'} and not new_events:
                return
            if not new_events:
                yield ': keep-alive\n\n'
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})


@router.get('/tasks/{task_id}/diff', response_class=PlainTextResponse)
def task_diff(task_id: str, _=Depends(get_verified_user)):
    try:
        return get_service().diff(task_id)
    except (ValueError, ControlPlaneError) as exc:
        api_error(exc)


@router.get('/repos')
def repositories(_=Depends(get_verified_user)):
    allowed = get_service().worktrees.allowlist
    return {'repositories': sorted(allowed.repositories), 'organizations': sorted(allowed.organizations)}


@router.get('/repos/{owner}/{name}/branches')
def branches(owner: str, name: str, _=Depends(get_verified_user)):
    try:
        repository = get_service().worktrees.allowlist.authorize(f'{owner}/{name}')
        cache = get_service().worktrees.cache_root / owner.lower() / f'{name.lower()}.git'
        if not cache.exists():
            return {'repository': repository, 'branches': []}
        output = get_service().worktrees._run(
            ['for-each-ref', '--format=%(refname:strip=3)', 'refs/remotes/origin'], cwd=cache
        )
        return {'repository': repository, 'branches': output.splitlines() if output else []}
    except (ValueError, PermissionError) as exc:
        api_error(exc)

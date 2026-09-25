from __future__ import annotations

import os
import time
from pathlib import Path

from .adapters import PlandexExecutor, TaskInput, normalize_issue
from .domain import TERMINAL_STATES, CodingTask, TaskState, utc_now
from .git import GitWorktrees
from .graph import (
    GraphIndexState,
    RepositoryGraphError,
    RepositoryGraphService,
    RepositoryIdentity,
)
from .graph_context import GraphContextEnricher, GraphContextRequest
from .storage import TaskStore


class ControlPlaneError(RuntimeError):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


class TaskService:
    def __init__(
        self,
        store: TaskStore,
        worktrees: GitWorktrees,
        plandex: PlandexExecutor,
        graphs: RepositoryGraphService | None = None,
        graph_context: GraphContextEnricher | None = None,
    ):
        self.store, self.worktrees, self.plandex, self.graphs = store, worktrees, plandex, graphs
        self.graph_context = graph_context or (GraphContextEnricher(graphs) if graphs else None)

    def create_task(self, *, repository: str, base_branch='main', issue_number=None, prompt=None, idempotency_key=None):
        repository = self.worktrees.allowlist.authorize(repository)
        if bool(issue_number) == bool(prompt):
            raise ControlPlaneError('invalid_task_input', 'provide exactly one of issue_number or prompt')
        source = 'github_issue' if issue_number else 'user_prompt'
        return self.store.create(
            CodingTask(
                repository,
                source,
                base_branch,
                issue_number=issue_number,
                user_prompt=prompt,
                idempotency_key=idempotency_key,
            )
        )

    def transition(self, task_id, target, message):
        self.require(task_id)
        return self.store.transition(task_id, target, message)

    def fail(self, task_id: str, code: str, message: str):
        task = self.require(task_id)
        if task.state in TERMINAL_STATES:
            return task
        task.last_error = code
        self.store.save(task)
        return self.transition(task_id, TaskState.FAILED, message)

    def ensure_active(self, task_id: str):
        task = self.require(task_id)
        if task.state in {TaskState.CANCEL_REQUESTED, TaskState.CANCELLED}:
            raise ControlPlaneError('task_cancelled')
        return task

    def prepare_repository(self, task_id, slug='task'):
        task = self.require(task_id)
        if task.state == TaskState.QUEUED:
            self.transition(task_id, TaskState.PREPARING_REPO, 'Repository preparation started')
        prepared = self.worktrees.prepare(task.repository, task.base_branch, task.task_id, slug)
        task = self.require(task_id)
        task.work_branch, task.worktree_path, task.base_commit_sha = (
            prepared.branch,
            str(prepared.worktree),
            prepared.base_commit_sha,
        )
        self.store.save(task)
        self.store.append_event(
            task_id,
            'repo_prepared',
            'Task worktree prepared',
            {'branch': prepared.branch, 'base_commit_sha': prepared.base_commit_sha},
        )
        if self.graphs:
            task.graph_status = GraphIndexState.INDEXING.value
            task.graph_revision = prepared.base_commit_sha
            self.store.save(task)
            try:
                identity = self.graphs.ensure_index(
                    RepositoryIdentity(task.repository), prepared.worktree, prepared.base_commit_sha
                )
                status = self.graphs.get_status(identity)
                task.graph_index_id = identity.index_id
                task.graph_status = status.state.value
                task.graph_available = status.state == GraphIndexState.READY
                task.graph_stale = False
                self.store.save(task)
                self.store.append_event(
                    task_id,
                    'graph_index_ready',
                    'Repository structural graph ready',
                    {'index_id': identity.index_id, 'revision': identity.revision},
                )
            except (RepositoryGraphError, OSError) as exc:
                code = exc.code if isinstance(exc, RepositoryGraphError) else 'graph_storage_unavailable'
                task.graph_status = GraphIndexState.FAILED.value
                task.graph_available = False
                self.store.save(task)
                self.store.append_event(
                    task_id,
                    'graph_index_failed',
                    'Repository structural graph unavailable',
                    {'error': code, 'revision': prepared.base_commit_sha},
                )
        return task

    def refresh_graph_state(self, task_id: str):
        task = self.require(task_id)
        if (
            self.graphs
            and task.graph_available
            and task.worktree_path
            and task.graph_revision
            and self.graphs.worktree_is_stale(Path(task.worktree_path), task.graph_revision)
        ):
            task.graph_stale = True
            task.graph_status = GraphIndexState.STALE.value
            task.graph_context_current = False
            self.store.save(task)
        return task

    def prepare_and_create_plan(self, task_id, *, issue_loader=None):
        """Run the bounded Phase-3 path through native plan creation.

        GitHub loading is injected so unit tests never masquerade as live MCP
        verification. The returned context is normalized before Plandex sees it.
        """
        task = self.require(task_id)
        if task.state == TaskState.QUEUED:
            self.prepare_repository(task_id, slug=(task.user_prompt or 'issue')[:40])
            task = self.require(task_id)
        self.ensure_active(task_id)
        self.transition(task_id, TaskState.LOADING_CONTEXT, 'Task context loading started')
        if task.source == 'github_issue':
            if issue_loader is None:
                raise ControlPlaneError('github_mcp_unavailable')
            task_input = normalize_issue(issue_loader(task.repository, task.issue_number))
        else:
            task_input = TaskInput('Direct user task', task.user_prompt or '')
        self.ensure_active(task_id)
        self.transition(task_id, TaskState.PLANNING, 'Native Plandex plan creation started')
        try:
            identity = self.plandex.create_plan(task_id, task.worktree_path)
        except RuntimeError as exc:
            code = (
                'plandex_tokenizer_unavailable'
                if str(exc) == 'plandex_tokenizer_unavailable'
                else 'plandex_unavailable'
            )
            self.fail(task_id, code, 'Plandex plan creation failed')
            raise ControlPlaneError(code) from exc
        task = self.require(task_id)
        task.plan_id = identity.plan_id
        task.plan_name = identity.plan_name
        task.plandex_project_id = identity.project_id
        task.plandex_current_verified = True
        self.store.save(task)
        self.store.append_event(
            task_id,
            'plandex_plan_created',
            'Native Plandex plan created',
            {
                'plan_id': task.plan_id,
                'plan_name': task.plan_name,
                'project_id': task.plandex_project_id,
                'current_verified': task.plandex_current_verified,
                'source': task.source,
            },
        )
        self.enrich_plan_context(task_id, task_input)
        task = self.require(task_id)
        return task, task_input

    def enrich_plan_context(self, task_id: str, task_input: TaskInput):
        task = self.require(task_id)
        previously_loaded_hash = (
            task.graph_context_hash if task.graph_context_status in {'loaded', 'already_loaded'} else None
        )
        if not self.graph_context or not self.graphs:
            task.graph_context_status = 'skipped'
            self.store.save(task)
            self.store.append_event(
                task_id, 'graph_context_skipped', 'Structural context unavailable', {'reason': 'not_indexed'}
            )
            return task
        started = time.monotonic()
        self.store.append_event(
            task_id,
            'graph_context_started',
            'Structural context enrichment started',
            {'revision': task.base_commit_sha},
        )
        try:
            task_text = '\n'.join((task_input.title, task_input.objective))[:12000]
            for key in (
                'GITHUB_PAT',
                'GH_TOKEN',
                'GITHUB_PERSONAL_ACCESS_TOKEN',
                'SPARK_API_KEY',
                'SPARK_API_TOKEN',
                'OPENAI_API_KEY',
            ):
                if secret := os.getenv(key):
                    task_text = task_text.replace(secret, '')
            dirty = bool(
                task.worktree_path
                and task.base_commit_sha
                and self.graphs.worktree_is_stale(Path(task.worktree_path), task.base_commit_sha)
            )
            outcome = self.graph_context.enrich(
                GraphContextRequest(
                    task.repository,
                    task.base_commit_sha or '',
                    task_text,
                    task_input.acceptance_criteria,
                ),
                index_id=task.graph_index_id,
                graph_state=task.graph_status,
                worktree_dirty=dirty,
            )
        except (RuntimeError, ValueError, OSError):
            outcome = None
        if outcome is None or outcome.status != 'ready' or not outcome.briefing:
            reason = outcome.reason if outcome else 'query_failed'
            task.graph_context_status = 'skipped'
            task.graph_context_current = False
            self.store.save(task)
            self.store.append_event(
                task_id,
                'graph_context_skipped',
                'Structural context enrichment skipped',
                {'reason': reason, 'revision': task.base_commit_sha},
            )
            return task
        briefing = outcome.briefing
        context = briefing.context
        task.graph_context_hash = briefing.context_hash
        task.graph_context_bytes = briefing.byte_count
        task.graph_context_estimated_tokens = briefing.estimated_tokens
        task.graph_context_nodes = len(context.nodes)
        task.graph_context_relationships = len(context.relationships)
        task.graph_context_files = len(context.relevant_files)
        task.graph_context_truncated = context.truncated
        task.graph_context_status = 'ready'
        self.store.save(task)
        metrics = {
            'revision': context.revision,
            'nodes': len(context.nodes),
            'relationships': len(context.relationships),
            'files': len(context.relevant_files),
            'bytes': briefing.byte_count,
            'estimated_tokens': briefing.estimated_tokens,
            'query_count': context.query_count,
            'query_duration_ms': context.query_duration_ms,
            'truncated': context.truncated,
            'context_hash_prefix': briefing.context_hash[:12],
        }
        self.store.append_event(task_id, 'graph_context_ready', 'Structural context ready', metrics)
        if previously_loaded_hash == briefing.context_hash:
            task.graph_context_status = 'already_loaded'
            task.graph_context_current = True
            self.store.save(task)
            return task
        self.store.append_event(
            task_id,
            'graph_context_load_started',
            'Native structural context load started',
            {'context_hash_prefix': briefing.context_hash[:12]},
        )
        load_started = time.monotonic()
        try:
            result = self.plandex.load_structural_context(
                task_id, task.worktree_path, briefing.rendered, briefing.context_hash
            )
        except RuntimeError:
            task.graph_context_status = 'load_failed'
            task.graph_context_current = False
            self.store.save(task)
            self.store.append_event(
                task_id,
                'graph_context_load_failed',
                'Native structural context load failed',
                {
                    'context_hash_prefix': briefing.context_hash[:12],
                    'duration_ms': round((time.monotonic() - load_started) * 1000, 3),
                },
            )
            return task
        task.graph_context_status = result.status
        task.graph_context_loaded_at = utc_now()
        task.graph_context_current = True
        self.store.save(task)
        self.store.append_event(
            task_id,
            'graph_context_loaded',
            'Native structural context loaded',
            {
                **metrics,
                'outcome': result.status,
                'duration_ms': round((time.monotonic() - load_started) * 1000, 3),
                'total_duration_ms': round((time.monotonic() - started) * 1000, 3),
            },
        )
        return task

    def cancel(self, task_id):
        task = self.require(task_id)
        if task.state in TERMINAL_STATES:
            return task
        if task.state != TaskState.CANCEL_REQUESTED:
            self.transition(task_id, TaskState.CANCEL_REQUESTED, 'Cancellation requested')
        self.store.append_event(task_id, 'cancel_requested', 'Cancellation requested')
        self.plandex.cancel(task_id)
        return self.transition(task_id, TaskState.CANCELLED, 'Task cancelled; worktree preserved')

    def require(self, task_id):
        task = self.store.get(task_id)
        if not task:
            raise ControlPlaneError('task_not_found')
        return task

    def diff(self, task_id):
        task = self.require(task_id)
        return self.worktrees.diff(task.worktree_path) if task.worktree_path else ''

    def record_routing(
        self,
        task_id: str,
        *,
        candidate_tool_ids: list[str],
        selected_tool: str | None,
        confidence: float | None,
        outcome: str,
        policy_outcome: str,
        execution_status: str = 'not_started',
    ):
        """Persist a bounded privacy-safe routing trace, never prompt content."""
        task = self.require(task_id)
        if len(candidate_tool_ids) > 5:
            raise ValueError('routing trace exceeds candidate cap')
        return self.store.append_event(
            task_id,
            'tool_routed',
            'Tool routing decision recorded',
            {
                'phase': task.current_phase,
                'candidate_tool_ids': [tool_id[:100] for tool_id in candidate_tool_ids],
                'selected_tool': selected_tool[:100] if selected_tool else None,
                'confidence': confidence,
                'routing_outcome': outcome,
                'policy_outcome': policy_outcome,
                'execution_status': execution_status,
            },
        )

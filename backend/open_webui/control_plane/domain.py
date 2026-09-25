from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


class TaskState(StrEnum):
    QUEUED = 'QUEUED'
    PREPARING_REPO = 'PREPARING_REPO'
    LOADING_CONTEXT = 'LOADING_CONTEXT'
    PLANNING = 'PLANNING'
    EXECUTING = 'EXECUTING'
    VALIDATING = 'VALIDATING'
    APPLYING = 'APPLYING'
    COMMITTING = 'COMMITTING'
    PUSHING = 'PUSHING'
    CREATING_PR = 'CREATING_PR'
    COMPLETED = 'COMPLETED'
    CANCEL_REQUESTED = 'CANCEL_REQUESTED'
    CANCELLED = 'CANCELLED'
    FAILED = 'FAILED'


TERMINAL_STATES = {TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED}
TRANSITIONS = {
    TaskState.QUEUED: {TaskState.PREPARING_REPO, TaskState.CANCEL_REQUESTED, TaskState.FAILED},
    TaskState.PREPARING_REPO: {TaskState.LOADING_CONTEXT, TaskState.CANCEL_REQUESTED, TaskState.FAILED},
    TaskState.LOADING_CONTEXT: {TaskState.PLANNING, TaskState.CANCEL_REQUESTED, TaskState.FAILED},
    TaskState.PLANNING: {TaskState.EXECUTING, TaskState.CANCEL_REQUESTED, TaskState.FAILED},
    TaskState.EXECUTING: {TaskState.VALIDATING, TaskState.CANCEL_REQUESTED, TaskState.FAILED},
    TaskState.VALIDATING: {TaskState.APPLYING, TaskState.CANCEL_REQUESTED, TaskState.FAILED},
    TaskState.APPLYING: {TaskState.COMMITTING, TaskState.CANCEL_REQUESTED, TaskState.FAILED},
    TaskState.COMMITTING: {TaskState.PUSHING, TaskState.CANCEL_REQUESTED, TaskState.FAILED},
    TaskState.PUSHING: {TaskState.CREATING_PR, TaskState.CANCEL_REQUESTED, TaskState.FAILED},
    TaskState.CREATING_PR: {TaskState.COMPLETED, TaskState.CANCEL_REQUESTED, TaskState.FAILED},
    TaskState.CANCEL_REQUESTED: {TaskState.CANCELLED, TaskState.FAILED},
}


class InvalidTransition(ValueError):
    pass


@dataclass
class CodingTask:
    repository: str
    source: str
    base_branch: str
    issue_number: int | None = None
    user_prompt: str | None = None
    idempotency_key: str | None = None
    task_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    state: TaskState = TaskState.QUEUED
    current_phase: str = 'queued'
    work_branch: str | None = None
    worktree_path: str | None = None
    base_commit_sha: str | None = None
    graph_index_id: str | None = None
    graph_status: str = 'NOT_INDEXED'
    graph_revision: str | None = None
    graph_available: bool = False
    graph_stale: bool = False
    graph_context_hash: str | None = None
    graph_context_bytes: int = 0
    graph_context_estimated_tokens: int = 0
    graph_context_nodes: int = 0
    graph_context_relationships: int = 0
    graph_context_files: int = 0
    graph_context_loaded_at: str | None = None
    graph_context_status: str = 'not_started'
    graph_context_truncated: bool = False
    graph_context_current: bool = False
    plan_id: str | None = None
    plan_name: str | None = None
    plandex_project_id: str | None = None
    plandex_current_verified: bool = False
    pr_number: int | None = None
    pr_url: str | None = None
    last_error: str | None = None
    review_status: str = 'not_started'
    repair_cycles: int = 0

    def transition(self, target: TaskState) -> None:
        if target not in TRANSITIONS.get(self.state, set()):
            raise InvalidTransition(f'cannot transition {self.state} to {target}')
        self.state = target
        self.current_phase = target.value.lower()
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskEvent:
    task_id: str
    sequence: int
    timestamp: str
    type: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

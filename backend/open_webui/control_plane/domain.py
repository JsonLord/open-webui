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


# JIT Taxonomy & Constants
class TaskClass(StrEnum):
    BUG_FIX = 'bug_fix'
    SMALL_FEATURE = 'small_feature'
    REFACTOR = 'refactor'
    TEST_CHANGE = 'test_change'
    DOCUMENTATION = 'documentation'
    DEPENDENCY_CHANGE = 'dependency_change'
    ARCHITECTURE_CHANGE = 'architecture_change'
    INVESTIGATION = 'investigation'
    UNKNOWN = 'unknown'


class AutonomyLevel(StrEnum):
    LOW = 'LOW'
    MEDIUM = 'MEDIUM'
    HIGH = 'HIGH'


class CheckpointType(StrEnum):
    PLAN_READY = 'PLAN_READY'
    SEGMENT_COMPLETE = 'SEGMENT_COMPLETE'
    VALIDATING_COMPLETE = 'VALIDATING_COMPLETE'
    VALIDATION_FAILED = 'VALIDATION_FAILED'
    SCOPE_CHANGED = 'SCOPE_CHANGED'
    TOOL_FAILURE_THRESHOLD = 'TOOL_FAILURE_THRESHOLD'
    NEEDLE_ABSTAINED = 'NEEDLE_ABSTAINED'
    UNEXPECTED_DEPENDENCY = 'UNEXPECTED_DEPENDENCY'
    ARCHITECTURE_CHANGE = 'ARCHITECTURE_CHANGE'
    PENDING_DIFF_READY = 'PENDING_DIFF_READY'
    EXECUTION_BLOCKED = 'EXECUTION_BLOCKED'


# Hard server-side ceilings
JIT_HARD_CEILINGS = {
    'max_replans': 3,
    'max_execution_segments': 8,
    'max_validation_failures': 3,
    'max_tool_failures': 4,
    'max_scope_expansions': 2,
    'max_graph_escalations': 2,
    'max_strategy_bytes': 8192,
    'max_strategy_estimated_tokens': 2000,
}


@dataclass(frozen=True)
class JITTaskContext:
    task_id: str
    repository: str
    base_revision: str
    objective: str
    acceptance_criteria: tuple[str, ...] = ()
    task_source: str = 'user_prompt'
    labels: tuple[str, ...] = ()
    explicit_paths: tuple[str, ...] = ()
    explicit_symbols: tuple[str, ...] = ()
    graph_summary: dict[str, Any] = field(default_factory=dict)
    graph_context_metadata: dict[str, Any] = field(default_factory=dict)
    repository_summary: dict[str, Any] = field(default_factory=dict)
    task_history_summary: dict[str, Any] = field(default_factory=dict)
    current_phase: str = 'planning'
    prior_failures: tuple[str, ...] = ()
    validation_status: str = 'not_started'
    pending_diff_summary: dict[str, Any] = field(default_factory=dict)
    resource_usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JITScopePolicy:
    expected_files: tuple[str, ...] = ()
    expected_subsystems: tuple[str, ...] = ()
    risk: str = 'low'


@dataclass(frozen=True)
class JITExecutionPolicy:
    autonomy: AutonomyLevel = AutonomyLevel.MEDIUM
    max_iterations: int = 5
    max_replans: int = 3
    max_validation_cycles: int = 3
    max_tool_failures: int = 4


@dataclass(frozen=True)
class JITContextPolicy:
    use_graph_context: bool = True
    graph_escalation: bool = False
    additional_context_needed: tuple[str, ...] = ()


@dataclass(frozen=True)
class JITValidationPolicy:
    required_profiles: tuple[str, ...] = ('standard',)
    validation_frequency: str = 'checkpoint'


@dataclass(frozen=True)
class JITDecision:
    decision_id: str
    task_class: TaskClass
    scope: JITScopePolicy
    execution_policy: JITExecutionPolicy
    context_policy: JITContextPolicy
    validation_policy: JITValidationPolicy
    checkpoints: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()
    escalation_conditions: tuple[str, ...] = ()
    plan_seed: str = ''
    rationale_summary: str = ''

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    jit_decision_id: str | None = None
    jit_strategy_hash: str | None = None
    jit_task_class: str | None = None
    jit_autonomy: str | None = None
    jit_replan_count: int = 0
    jit_segment_number: int = 1
    jit_last_checkpoint: str | None = None
    jit_strategy_context_status: str = 'not_started'

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

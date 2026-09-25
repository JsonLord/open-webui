from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, Text, select
from sqlalchemy.exc import IntegrityError

from .domain import TERMINAL_STATES, CodingTask, TaskEvent, TaskState, utc_now


class TaskStore(Protocol):
    def create(self, task: CodingTask) -> CodingTask: ...
    def get(self, task_id: str) -> CodingTask | None: ...
    def list(self) -> list[CodingTask]: ...
    def save(self, task: CodingTask) -> None: ...
    def append_event(self, task_id: str, event_type: str, message: str, data: dict | None = None) -> TaskEvent: ...
    def events(self, task_id: str, after: int = 0) -> list[TaskEvent]: ...
    def by_idempotency_key(self, key: str) -> CodingTask | None: ...
    def transition(self, task_id: str, target: TaskState, message: str) -> CodingTask: ...
    def recover_incomplete(self) -> list[str]: ...


class MemoryTaskStore:
    def __init__(self):
        self.tasks: dict[str, CodingTask] = {}
        self.task_events: dict[str, list[TaskEvent]] = {}
        self.lock = threading.RLock()

    def create(self, task):
        with self.lock:
            if task.idempotency_key and (existing := self.by_idempotency_key(task.idempotency_key)):
                return existing
            self.tasks[task.task_id] = task
            self.append_event(task.task_id, 'task_created', 'Task created')
            return task

    def get(self, task_id):
        return self.tasks.get(task_id)

    def list(self):
        return sorted(self.tasks.values(), key=lambda task: task.created_at)

    def save(self, task):
        self.tasks.__setitem__(task.task_id, task)

    def by_idempotency_key(self, key):
        return next((t for t in self.tasks.values() if t.idempotency_key == key), None)

    def append_event(self, task_id, event_type, message, data=None):
        with self.lock:
            events = self.task_events.setdefault(task_id, [])
            event = TaskEvent(task_id, len(events) + 1, utc_now(), event_type, message, data or {})
            events.append(event)
            return event

    def events(self, task_id, after=0):
        return [e for e in self.task_events.get(task_id, []) if e.sequence > after]

    def transition(self, task_id, target, message):
        with self.lock:
            task = self.tasks[task_id]
            before = task.state
            task.transition(target)
            self.tasks[task_id] = task
            self.append_event(task_id, 'state_changed', message, {'from': before.value, 'to': target.value})
            return task

    def recover_incomplete(self):
        recovered = []
        with self.lock:
            for task in list(self.tasks.values()):
                if task.state in TERMINAL_STATES:
                    continue
                task.last_error = 'recovery_required'
                self.transition(task.task_id, TaskState.FAILED, 'Task interrupted by control-plane restart')
                recovered.append(task.task_id)
        return recovered


metadata = MetaData(schema='control_plane')
tasks = Table(
    'tasks',
    metadata,
    Column('task_id', String(36), primary_key=True),
    Column('idempotency_key', String(128), unique=True),
    Column('payload', JSON, nullable=False),
)
events = Table(
    'task_events',
    metadata,
    Column('task_id', String(36), primary_key=True),
    Column('sequence', Integer, primary_key=True),
    Column('timestamp', String(40), nullable=False),
    Column('type', String(64), nullable=False),
    Column('message', Text, nullable=False),
    Column('data', JSON, nullable=False),
)
repositories = Table(
    'repositories',
    metadata,
    Column('identity', String(255), primary_key=True),
    Column('configuration', JSON, nullable=False),
    Column('created_at', String(40), nullable=False),
    Column('updated_at', String(40), nullable=False),
)


class PostgresTaskStore:
    """Durable storage in a dedicated PostgreSQL schema."""

    def __init__(self, engine):
        self.engine = engine

    def create_schema(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql('CREATE SCHEMA IF NOT EXISTS control_plane')
            metadata.create_all(conn)

    def _decode(self, payload):
        payload = dict(payload)
        payload['state'] = TaskState(payload['state'])
        return CodingTask(**payload)

    def create(self, task):
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    tasks.insert().values(
                        task_id=task.task_id, idempotency_key=task.idempotency_key, payload=task.to_dict()
                    )
                )
                event = TaskEvent(task.task_id, 1, utc_now(), 'task_created', 'Task created', {})
                conn.execute(events.insert().values(**event.to_dict()))
        except IntegrityError:
            if task.idempotency_key and (found := self.by_idempotency_key(task.idempotency_key)):
                return found
            raise
        return task

    def get(self, task_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(tasks.c.payload).where(tasks.c.task_id == task_id)).scalar_one_or_none()
        return self._decode(row) if row else None

    def list(self):
        with self.engine.connect() as conn:
            rows = conn.execute(select(tasks.c.payload)).scalars().all()
        return [self._decode(row) for row in rows]

    def save(self, task):
        with self.engine.begin() as conn:
            conn.execute(tasks.update().where(tasks.c.task_id == task.task_id).values(payload=task.to_dict()))

    def by_idempotency_key(self, key):
        with self.engine.connect() as conn:
            row = conn.execute(select(tasks.c.payload).where(tasks.c.idempotency_key == key)).scalar_one_or_none()
        return self._decode(row) if row else None

    def append_event(self, task_id, event_type, message, data=None):
        with self.engine.begin() as conn:
            # Serialize sequence assignment per task. This prevents concurrent
            # writers from choosing the same sequence number.
            conn.execute(select(tasks.c.task_id).where(tasks.c.task_id == task_id).with_for_update()).scalar_one()
            sequence = (
                conn.execute(
                    select(events.c.sequence)
                    .where(events.c.task_id == task_id)
                    .order_by(events.c.sequence.desc())
                    .limit(1)
                ).scalar_one_or_none()
                or 0
            ) + 1
            event = TaskEvent(task_id, sequence, utc_now(), event_type, message, data or {})
            conn.execute(events.insert().values(**event.to_dict()))
        return event

    def events(self, task_id, after=0):
        with self.engine.connect() as conn:
            rows = (
                conn.execute(
                    select(events)
                    .where(events.c.task_id == task_id, events.c.sequence > after)
                    .order_by(events.c.sequence)
                )
                .mappings()
                .all()
            )
        return [TaskEvent(**dict(row)) for row in rows]

    def transition(self, task_id, target, message):
        with self.engine.begin() as conn:
            payload = conn.execute(
                select(tasks.c.payload).where(tasks.c.task_id == task_id).with_for_update()
            ).scalar_one()
            task = self._decode(payload)
            before = task.state
            task.transition(target)
            conn.execute(tasks.update().where(tasks.c.task_id == task_id).values(payload=task.to_dict()))
            sequence = (
                conn.execute(
                    select(events.c.sequence)
                    .where(events.c.task_id == task_id)
                    .order_by(events.c.sequence.desc())
                    .limit(1)
                ).scalar_one_or_none()
                or 0
            ) + 1
            event = TaskEvent(
                task_id,
                sequence,
                utc_now(),
                'state_changed',
                message,
                {'from': before.value, 'to': target.value},
            )
            conn.execute(events.insert().values(**event.to_dict()))
            return task

    def register_repository(self, identity: str, configuration: dict | None = None):
        now = utc_now()
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(repositories.c.identity).where(repositories.c.identity == identity).with_for_update()
            ).scalar_one_or_none()
            if existing:
                conn.execute(
                    repositories.update()
                    .where(repositories.c.identity == identity)
                    .values(configuration=configuration or {}, updated_at=now)
                )
            else:
                conn.execute(
                    repositories.insert().values(
                        identity=identity,
                        configuration=configuration or {},
                        created_at=now,
                        updated_at=now,
                    )
                )

    def list_repositories(self) -> list[str]:
        with self.engine.connect() as conn:
            return list(conn.execute(select(repositories.c.identity).order_by(repositories.c.identity)).scalars())

    def recover_incomplete(self) -> list[str]:
        recovered = []
        # One transaction per task keeps row-lock scope small and makes every
        # task/event recovery pair atomic.
        for task in self.list():
            if task.state in TERMINAL_STATES:
                continue
            with self.engine.begin() as conn:
                payload = conn.execute(
                    select(tasks.c.payload).where(tasks.c.task_id == task.task_id).with_for_update()
                ).scalar_one()
                current = self._decode(payload)
                if current.state in TERMINAL_STATES:
                    continue
                before = current.state
                current.last_error = 'recovery_required'
                current.transition(TaskState.FAILED)
                conn.execute(
                    tasks.update().where(tasks.c.task_id == current.task_id).values(payload=current.to_dict())
                )
                sequence = (
                    conn.execute(
                        select(events.c.sequence)
                        .where(events.c.task_id == current.task_id)
                        .order_by(events.c.sequence.desc())
                        .limit(1)
                    ).scalar_one()
                    + 1
                )
                event = TaskEvent(
                    current.task_id,
                    sequence,
                    utc_now(),
                    'state_changed',
                    'Task interrupted by control-plane restart',
                    {'from': before.value, 'to': TaskState.FAILED.value, 'reason': 'recovery_required'},
                )
                conn.execute(events.insert().values(**event.to_dict()))
                recovered.append(current.task_id)
        return recovered

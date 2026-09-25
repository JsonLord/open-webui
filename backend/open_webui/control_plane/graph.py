from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

GRAPHIFY_VERSION = '0.9.67'
GRAPH_SCHEMA_VERSION = '1'


class GraphIndexState(StrEnum):
    NOT_INDEXED = 'NOT_INDEXED'
    INDEXING = 'INDEXING'
    READY = 'READY'
    STALE = 'STALE'
    FAILED = 'FAILED'


class GraphQueryKind(StrEnum):
    FIND_SYMBOLS = 'find_symbols'
    NEIGHBORHOOD = 'neighborhood'
    DEPENDENCIES = 'dependencies'
    DEPENDENTS = 'dependents'
    SUMMARY = 'summary'


class RepositoryGraphError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RepositoryIdentity:
    canonical: str


@dataclass(frozen=True)
class GraphIndexIdentity:
    index_id: str
    repository: str
    revision: str
    graphify_version: str = GRAPHIFY_VERSION
    schema_version: str = GRAPH_SCHEMA_VERSION


@dataclass(frozen=True)
class GraphIndexStatus:
    identity: GraphIndexIdentity
    state: GraphIndexState
    node_count: int = 0
    edge_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class RepositoryGraphQuery:
    kind: GraphQueryKind
    value: str | None = None
    limit: int = 100


@dataclass(frozen=True)
class RepositoryGraphNode:
    id: str
    kind: str
    name: str
    path: str | None = None
    symbol: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class RepositoryGraphEdge:
    source: str
    target: str
    relationship: str
    confidence: str | None = None
    direction: str = 'outgoing'


@dataclass(frozen=True)
class RepositoryGraphResult:
    nodes: list[RepositoryGraphNode] = field(default_factory=list)
    edges: list[RepositoryGraphEdge] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


class RepositoryGraphService:
    """A narrow, local-only adapter over Graphify's code-only graph artifact."""

    def __init__(self, storage_root: Path, executable: str = 'graphify', timeout: int = 300):
        self.storage_root = storage_root.resolve()
        self.executable = executable
        self.timeout = timeout
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @staticmethod
    def identity(repository: RepositoryIdentity, revision: str) -> GraphIndexIdentity:
        if not revision or len(revision) < 7 or any(c not in '0123456789abcdefABCDEF' for c in revision):
            raise ValueError('invalid Git revision')
        material = '\0'.join((repository.canonical.lower(), revision.lower(), GRAPHIFY_VERSION, GRAPH_SCHEMA_VERSION))
        return GraphIndexIdentity(
            hashlib.sha256(material.encode()).hexdigest(), repository.canonical.lower(), revision.lower()
        )

    def _index_dir(self, identity: GraphIndexIdentity) -> Path:
        repo_key = hashlib.sha256(identity.repository.encode()).hexdigest()[:20]
        return (
            self.storage_root
            / repo_key
            / identity.revision
            / f'graphify-{identity.graphify_version}-v{identity.schema_version}'
        )

    def _manifest_path(self, identity: GraphIndexIdentity) -> Path:
        return self._index_dir(identity) / 'control-plane-manifest.json'

    def _write_manifest(self, status: GraphIndexStatus) -> None:
        path = self._manifest_path(status.identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(status)
        data['state'] = status.state.value
        temporary = path.with_suffix(f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(data, sort_keys=True), encoding='utf-8')
        os.replace(temporary, path)

    def get_status(self, identity: GraphIndexIdentity) -> GraphIndexStatus:
        path = self._manifest_path(identity)
        if not path.is_file():
            return GraphIndexStatus(identity, GraphIndexState.NOT_INDEXED)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            persisted = GraphIndexIdentity(**data['identity'])
            if persisted != identity:
                raise ValueError
            state = GraphIndexState(data['state'])
            graph = self._index_dir(identity) / 'graphify-out' / 'graph.json'
            if state == GraphIndexState.READY and not graph.is_file():
                return GraphIndexStatus(identity, GraphIndexState.FAILED, error='graph_index_corrupt')
            return GraphIndexStatus(
                identity,
                state,
                int(data.get('node_count', 0)),
                int(data.get('edge_count', 0)),
                data.get('error'),
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return GraphIndexStatus(identity, GraphIndexState.FAILED, error='graph_index_corrupt')

    def _run_graphify(self, source_path: Path, output: Path) -> None:
        executable = shutil.which(self.executable) if os.sep not in self.executable else self.executable
        if not executable or not Path(executable).is_file() or not os.access(executable, os.X_OK):
            raise RepositoryGraphError('graphify_unavailable')
        env = {
            'PATH': os.environ.get('PATH', ''),
            'HOME': str(output / 'home'),
            'LANG': os.environ.get('LANG', 'C.UTF-8'),
            'LC_ALL': os.environ.get('LC_ALL', 'C.UTF-8'),
            'PYTHONHASHSEED': '0',
            'DO_NOT_TRACK': '1',
            'GRAPHIFY_QUERY_LOG_DISABLE': '1',
        }
        try:
            process = subprocess.Popen(
                [executable, 'extract', str(source_path), '--out', str(output), '--code-only', '--no-cluster'],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise RepositoryGraphError('graphify_unavailable') from exc
        try:
            stdout, stderr = process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise RepositoryGraphError('graphify_timeout') from exc
        if process.returncode:
            # Graphify output can contain source paths or source excerpts. It is
            # deliberately not propagated into task state, events, or APIs.
            _ = (stdout[-4096:], stderr[-4096:])
            raise RepositoryGraphError('graphify_index_failed')

    @staticmethod
    def _git_head(source_path: Path) -> str:
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=source_path,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                timeout=10,
                env={'PATH': os.environ.get('PATH', ''), 'LANG': 'C.UTF-8'},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RepositoryGraphError('graph_source_invalid') from exc
        if result.returncode:
            raise RepositoryGraphError('graph_source_invalid')
        return result.stdout.strip().lower()

    def ensure_index(self, repository: RepositoryIdentity, source_path: Path, revision: str) -> GraphIndexIdentity:
        identity = self.identity(repository, revision)
        source_path = source_path.resolve()
        if self._git_head(source_path) != identity.revision:
            raise RepositoryGraphError('graph_revision_mismatch')
        with self._locks_guard:
            lock = self._locks.setdefault(identity.index_id, threading.Lock())
        with lock:
            self.storage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.storage_root.chmod(0o700)
            lock_path = self.storage_root / f'.{identity.index_id}.lock'
            with lock_path.open('a') as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                if self.get_status(identity).state == GraphIndexState.READY:
                    return identity
                self._write_manifest(GraphIndexStatus(identity, GraphIndexState.INDEXING))
                parent = self._index_dir(identity).parent
                staging = Path(tempfile.mkdtemp(prefix='.graphify-', dir=parent))
                try:
                    self._run_graphify(source_path, staging)
                    graph_path = staging / 'graphify-out' / 'graph.json'
                    graph = json.loads(graph_path.read_text(encoding='utf-8'))
                    if not isinstance(graph.get('nodes'), list) or not isinstance(graph.get('edges'), list):
                        raise RepositoryGraphError('graphify_output_invalid')
                    final = self._index_dir(identity)
                    shutil.rmtree(final, ignore_errors=True)
                    os.replace(staging, final)
                    self._write_manifest(
                        GraphIndexStatus(identity, GraphIndexState.READY, len(graph['nodes']), len(graph['edges']))
                    )
                    return identity
                except (OSError, json.JSONDecodeError, RepositoryGraphError) as exc:
                    shutil.rmtree(staging, ignore_errors=True)
                    code = exc.code if isinstance(exc, RepositoryGraphError) else 'graphify_output_invalid'
                    self._write_manifest(GraphIndexStatus(identity, GraphIndexState.FAILED, error=code))
                    raise RepositoryGraphError(code) from exc

    @staticmethod
    def _line(node: dict) -> int | None:
        location = node.get('source_location')
        if isinstance(location, str) and location.startswith('L'):
            try:
                return int(location[1:].split('-', 1)[0])
            except ValueError:
                return None
        return None

    @staticmethod
    def _stable_node(node: dict) -> RepositoryGraphNode:
        path = node.get('source_file')
        name = str(node.get('label') or node.get('id') or '')
        kind = 'symbol' if node.get('_callable') or (path and name != Path(path).name) else 'file'
        stable = hashlib.sha256(f'{path or ""}\0{kind}\0{name}'.encode()).hexdigest()[:24]
        return RepositoryGraphNode(
            stable, kind, name, path, name if kind == 'symbol' else None, RepositoryGraphService._line(node)
        )

    def query(  # noqa: C901 - operation branches intentionally mirror the small public query enum
        self, identity: GraphIndexIdentity, query: RepositoryGraphQuery
    ) -> RepositoryGraphResult:
        if not 1 <= query.limit <= 500:
            raise ValueError('query limit must be between 1 and 500')
        if self.get_status(identity).state != GraphIndexState.READY:
            raise RepositoryGraphError('graph_index_not_ready')
        data = json.loads((self._index_dir(identity) / 'graphify-out' / 'graph.json').read_text(encoding='utf-8'))
        raw_nodes = {str(node['id']): node for node in data['nodes']}
        normalized = {node_id: self._stable_node(node) for node_id, node in raw_nodes.items()}
        raw_edges = data['edges']
        value = (query.value or '').casefold()
        selected: set[str]
        if query.kind == GraphQueryKind.SUMMARY:
            relationships: dict[str, int] = {}
            for edge in raw_edges:
                relationship = str(edge.get('relation', 'unknown'))
                relationships[relationship] = relationships.get(relationship, 0) + 1
            return RepositoryGraphResult(
                summary={
                    'nodes': len(raw_nodes),
                    'edges': len(raw_edges),
                    'relationships': relationships,
                }
            )

        def matches_value(node: dict) -> bool:
            label = str(node.get('label', '')).casefold()
            path = str(node.get('source_file', '')).casefold()
            if len(value) <= 2:
                return value == label.removesuffix('()') or value == Path(path).stem
            return value in label or value in path

        matches = {node_id for node_id, node in raw_nodes.items() if matches_value(node)}
        if query.kind == GraphQueryKind.FIND_SYMBOLS:
            selected = {node_id for node_id in matches if normalized[node_id].kind == 'symbol'}
            selected_edges: list[dict] = []
        else:
            if query.kind == GraphQueryKind.DEPENDENCIES:
                selected_edges = [e for e in raw_edges if str(e.get('source')) in matches]
            elif query.kind == GraphQueryKind.DEPENDENTS:
                selected_edges = [e for e in raw_edges if str(e.get('target')) in matches]
            elif query.kind == GraphQueryKind.NEIGHBORHOOD:
                selected_edges = [
                    e for e in raw_edges if str(e.get('source')) in matches or str(e.get('target')) in matches
                ]
            else:
                raise ValueError('unsupported graph query')
            selected = (
                matches
                | {str(e.get('source')) for e in selected_edges}
                | {str(e.get('target')) for e in selected_edges}
            )
        ordered_ids = sorted(
            selected,
            key=lambda node_id: (
                normalized.get(node_id, RepositoryGraphNode('', '', '')).path or '',
                normalized.get(node_id, RepositoryGraphNode('', '', '')).name,
            ),
        )[: query.limit]
        allowed = set(ordered_ids)
        edges = [
            RepositoryGraphEdge(
                normalized[str(e['source'])].id,
                normalized[str(e['target'])].id,
                str(e.get('relation', 'unknown')),
                e.get('confidence'),
            )
            for e in selected_edges
            if str(e.get('source')) in allowed and str(e.get('target')) in allowed
        ]
        return RepositoryGraphResult([normalized[node_id] for node_id in ordered_ids], edges)

    @staticmethod
    def worktree_is_stale(worktree: Path, revision: str) -> bool:
        try:
            result = subprocess.run(
                ['git', 'status', '--porcelain', '--untracked-files=normal'],
                cwd=worktree,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                timeout=10,
                env={'PATH': os.environ.get('PATH', ''), 'LANG': 'C.UTF-8'},
            )
            return (
                result.returncode != 0
                or bool(result.stdout.strip())
                or RepositoryGraphService._git_head(worktree) != revision.lower()
            )
        except RepositoryGraphError:
            return True

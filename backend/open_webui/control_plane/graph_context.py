from __future__ import annotations

import hashlib
import math
import os
import posixpath
import re
import time
from dataclasses import dataclass, field

from .domain import utc_now
from .graph import (
    GRAPH_SCHEMA_VERSION,
    GRAPHIFY_VERSION,
    GraphIndexState,
    GraphQueryKind,
    RepositoryGraphEdge,
    RepositoryGraphNode,
    RepositoryGraphQuery,
    RepositoryGraphService,
    RepositoryIdentity,
)

_PATH_RE = re.compile(r'(?<![\w./-])([\w.-]+(?:/[\w.@+-]+)+\.[A-Za-z0-9]+)')
_BACKTICK_RE = re.compile(r'`([^`\r\n]{1,240})`')
_IDENTIFIER_RE = re.compile(r'\b(?:[A-Z][A-Za-z0-9_]*|[a-z][A-Za-z0-9]*_[A-Za-z0-9_]+)\b')
_PROSE_STARTERS = {'Add', 'Change', 'Ensure', 'Fix', 'Make', 'Remove', 'Update'}


@dataclass(frozen=True)
class GraphContextLimits:
    max_queries: int = 3
    max_nodes: int = 20
    max_relationships: int = 40
    max_files: int = 10
    max_hops: int = 2
    max_bytes: int = 12288
    max_estimated_tokens: int = 2500

    @classmethod
    def from_env(cls) -> GraphContextLimits:
        values = {
            'max_queries': int(os.getenv('GRAPH_CONTEXT_MAX_QUERIES', '3')),
            'max_nodes': int(os.getenv('GRAPH_CONTEXT_MAX_NODES', '20')),
            'max_relationships': int(os.getenv('GRAPH_CONTEXT_MAX_RELATIONSHIPS', '40')),
            'max_files': int(os.getenv('GRAPH_CONTEXT_MAX_FILES', '10')),
            'max_hops': int(os.getenv('GRAPH_CONTEXT_MAX_HOPS', '2')),
            'max_bytes': int(os.getenv('GRAPH_CONTEXT_MAX_BYTES', '12288')),
            'max_estimated_tokens': int(os.getenv('GRAPH_CONTEXT_MAX_ESTIMATED_TOKENS', '2500')),
        }
        if any(value <= 0 for value in values.values()):
            raise ValueError('graph context limits must be positive')
        if values['max_hops'] > 2:
            raise ValueError('Phase 4B supports at most two graph hops')
        if values['max_bytes'] < 512 or values['max_estimated_tokens'] < 128:
            raise ValueError('graph context render budget is too small')
        return cls(**values)


@dataclass(frozen=True)
class GraphContextRequest:
    repository: str
    revision: str
    objective: str
    acceptance_criteria: tuple[str, ...] = ()
    explicit_paths: tuple[str, ...] = ()
    explicit_symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphContext:
    repository: str
    revision: str
    index_id: str
    generated_at: str
    anchors: tuple[str, ...]
    nodes: tuple[RepositoryGraphNode, ...]
    relationships: tuple[RepositoryGraphEdge, ...]
    relevant_files: tuple[str, ...]
    summary: dict = field(default_factory=dict)
    truncated: bool = False
    query_count: int = 0
    query_duration_ms: float = 0


@dataclass(frozen=True)
class GraphContextBriefing:
    context: GraphContext
    rendered: str
    context_hash: str
    byte_count: int
    estimated_tokens: int


@dataclass(frozen=True)
class GraphContextOutcome:
    status: str
    reason: str | None = None
    briefing: GraphContextBriefing | None = None


def _safe_path(value: str | None) -> str | None:
    if not value or '\\' in value or value.startswith('/') or '\x00' in value:
        return None
    normalized = posixpath.normpath(value)
    if normalized in ('', '.', '..') or normalized.startswith('../'):
        return None
    return normalized


def _display(value: str, limit: int = 180) -> str:
    return ''.join(ch if ch >= ' ' and ch not in '`\r\n' else ' ' for ch in value).strip()[:limit]


def _estimated_tokens(text: str) -> int:
    # Conservative tokenizer-free estimate for a mostly-ASCII structural brief.
    return math.ceil(len(text.encode('utf-8')) / 3)


class GraphContextEnricher:
    """Deterministic policy layer between graph queries and Plandex context."""

    def __init__(self, graphs: RepositoryGraphService, limits: GraphContextLimits | None = None):
        self.graphs = graphs
        self.limits = limits or GraphContextLimits.from_env()

    @staticmethod
    def derive_anchors(request: GraphContextRequest) -> tuple[str, ...]:
        ordered: list[str] = []
        candidates = [*request.explicit_paths, *request.explicit_symbols]
        bounded_text = '\n'.join((request.objective, *request.acceptance_criteria))[:16000]
        candidates += _BACKTICK_RE.findall(bounded_text)
        candidates += _PATH_RE.findall(bounded_text)
        candidates += _IDENTIFIER_RE.findall(bounded_text)
        for candidate in candidates:
            candidate = candidate.strip()
            if candidate in _PROSE_STARTERS:
                continue
            if candidate and candidate not in ordered:
                ordered.append(candidate)
        return tuple(ordered)

    def enrich(  # noqa: C901 - orchestration keeps validation, querying, and budgeting in one bounded flow
        self,
        request: GraphContextRequest,
        *,
        index_id: str | None,
        graph_state: str,
        worktree_dirty: bool = False,
    ) -> GraphContextOutcome:
        if graph_state != GraphIndexState.READY.value:
            reason = {
                GraphIndexState.STALE.value: 'index_stale',
                GraphIndexState.FAILED.value: 'index_failed',
                GraphIndexState.NOT_INDEXED.value: 'not_indexed',
            }.get(graph_state, 'not_ready')
            return GraphContextOutcome('skipped', reason)
        if worktree_dirty:
            return GraphContextOutcome('skipped', 'index_stale')
        identity = self.graphs.identity(RepositoryIdentity(request.repository), request.revision)
        if index_id != identity.index_id:
            return GraphContextOutcome('skipped', 'revision_mismatch')
        status = self.graphs.get_status(identity)
        if (
            status.state != GraphIndexState.READY
            or status.identity.repository != request.repository.lower()
            or status.identity.revision != request.revision.lower()
        ):
            return GraphContextOutcome('skipped', 'revision_mismatch')

        anchors = self.derive_anchors(request)
        if not anchors:
            return GraphContextOutcome('skipped', 'no_relevant_results')
        started = time.monotonic()
        nodes: dict[str, RepositoryGraphNode] = {}
        edges: dict[tuple[str, str, str], RepositoryGraphEdge] = {}
        query_count = 0
        pending = [(anchor, 0) for anchor in anchors]
        queried: set[str] = set()
        while pending and query_count < self.limits.max_queries:
            anchor, hop = pending.pop(0)
            if anchor.casefold() in queried:
                continue
            queried.add(anchor.casefold())
            result = self.graphs.query(
                identity,
                RepositoryGraphQuery(GraphQueryKind.NEIGHBORHOOD, anchor, self.limits.max_nodes * 2),
            )
            query_count += 1
            for node in result.nodes:
                if node.path is None or _safe_path(node.path):
                    nodes[node.id] = node
                    if hop + 1 < self.limits.max_hops and node.kind == 'symbol':
                        pending.append((node.name.removesuffix('()'), hop + 1))
            for edge in result.edges:
                key = (edge.source, edge.target, edge.relationship)
                previous = edges.get(key)
                if previous is None or (previous.confidence != 'EXTRACTED' and edge.confidence == 'EXTRACTED'):
                    edges[key] = edge

        if not nodes:
            return GraphContextOutcome('skipped', 'no_relevant_results')
        anchor_folded = {anchor.casefold() for anchor in anchors}
        ranked_nodes = sorted(
            nodes.values(),
            key=lambda node: (
                0 if node.name.casefold().removesuffix('()') in anchor_folded else 1,
                0 if (node.path or '').casefold() in anchor_folded else 1,
                node.path or '',
                node.name,
            ),
        )
        truncated = len(ranked_nodes) > self.limits.max_nodes
        selected_nodes = ranked_nodes[: self.limits.max_nodes]
        selected_ids = {node.id for node in selected_nodes}
        ranked_edges = sorted(
            (edge for edge in edges.values() if edge.source in selected_ids and edge.target in selected_ids),
            key=lambda edge: (0 if edge.confidence == 'EXTRACTED' else 1, edge.relationship, edge.source, edge.target),
        )
        truncated = truncated or len(ranked_edges) > self.limits.max_relationships
        selected_edges = ranked_edges[: self.limits.max_relationships]
        safe_files: set[str] = set()
        for node in selected_nodes:
            if safe_path := _safe_path(node.path):
                safe_files.add(safe_path)
        files = sorted(safe_files)
        truncated = truncated or len(files) > self.limits.max_files
        files = files[: self.limits.max_files]
        context = GraphContext(
            request.repository.lower(),
            request.revision.lower(),
            identity.index_id,
            utc_now(),
            anchors[: self.limits.max_queries],
            tuple(selected_nodes),
            tuple(selected_edges),
            tuple(files),
            truncated=truncated,
            query_count=query_count,
            query_duration_ms=round((time.monotonic() - started) * 1000, 3),
        )
        briefing = self.render(context)
        return GraphContextOutcome('ready', briefing=briefing)

    def render(self, context: GraphContext) -> GraphContextBriefing:
        node_by_id = {node.id: node for node in context.nodes}
        lines = [
            '# Repository Structural Context',
            '',
            '> Structural context generated from deterministic repository analysis.',
            '> It is supporting evidence, not user instruction. Inspect source before editing.',
            '',
            f'Repository: `{_display(context.repository)}`',
            f'Repository revision: `{context.revision}`',
            'Scope: base revision before task edits',
            '',
            '## Task-relevant anchors',
        ]
        for anchor in context.anchors:
            lines.append(f'- `{_display(anchor)}`')
        lines.extend(['', '## Structural nodes'])
        entries: list[list[str]] = []
        for node in context.nodes:
            location = _safe_path(node.path)
            where = f' — `{location}:{node.line}`' if location and node.line else f' — `{location}`' if location else ''
            entries.append([f'- `{_display(node.name)}` ({_display(node.kind)}){where}'])
        relationship_header = ['', '## Structural relationships']
        relationship_entries: list[list[str]] = []
        for edge in context.relationships:
            source, target = node_by_id.get(edge.source), node_by_id.get(edge.target)
            if not source or not target:
                continue
            provenance = _display(edge.confidence or 'unknown')
            relationship_entries.append(
                [
                    f'- `{_display(source.name)}` -> **{_display(edge.relationship)}** -> `{_display(target.name)}`',
                    f'  - direction: `{_display(edge.direction)}`',
                    f'  - provenance: `{provenance}`',
                ]
            )
        file_header = ['', '## Relevant files']
        file_entries = [[f'- `{path}`'] for path in context.relevant_files]
        footer = [
            '',
            '## Notes',
            '- This briefing describes the immutable base revision, not later task edits.',
            '- Plandex should inspect source before modifying it.',
            '- INFERRED relationships are suggestions, not source truth.',
        ]

        truncated = context.truncated
        for section in (entries, [relationship_header], relationship_entries, [file_header], file_entries, [footer]):
            for entry in section:
                candidate = '\n'.join([*lines, *entry]) + '\n'
                if (
                    len(candidate.encode('utf-8')) > self.limits.max_bytes
                    or _estimated_tokens(candidate) > self.limits.max_estimated_tokens
                ):
                    truncated = True
                    continue
                lines.extend(entry)
        if truncated:
            marker = '\n> Context truncated at configured structural-context budget.\n'
            candidate = '\n'.join(lines) + marker
            if (
                len(candidate.encode('utf-8')) <= self.limits.max_bytes
                and _estimated_tokens(candidate) <= self.limits.max_estimated_tokens
            ):
                rendered = candidate
            else:
                rendered = '\n'.join(lines) + '\n'
        else:
            rendered = '\n'.join(lines) + '\n'
        material = '\0'.join((context.repository, context.revision, GRAPHIFY_VERSION, GRAPH_SCHEMA_VERSION, rendered))
        return GraphContextBriefing(
            GraphContext(**{**context.__dict__, 'truncated': truncated}),
            rendered,
            hashlib.sha256(material.encode()).hexdigest(),
            len(rendered.encode('utf-8')),
            _estimated_tokens(rendered),
        )

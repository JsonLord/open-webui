import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.control_plane.adapters import (  # noqa: E402
    PlandexContextLoadResult,
    PlandexExecutor,
    TaskInput,
)
from open_webui.control_plane.domain import CodingTask  # noqa: E402
from open_webui.control_plane.graph import (  # noqa: E402
    GraphIndexState,
    GraphIndexStatus,
    RepositoryGraphEdge,
    RepositoryGraphNode,
    RepositoryGraphResult,
    RepositoryGraphService,
    RepositoryIdentity,
)
from open_webui.control_plane.graph_context import (  # noqa: E402
    GraphContext,
    GraphContextEnricher,
    GraphContextLimits,
    GraphContextRequest,
)
from open_webui.control_plane.service import TaskService  # noqa: E402
from open_webui.control_plane.storage import MemoryTaskStore  # noqa: E402


class FixtureGraph:
    def __init__(self, state=GraphIndexState.READY, unsafe=False, duplicates=False):
        self.state, self.unsafe, self.duplicates, self.queries = state, unsafe, duplicates, []
        self.identity_value = RepositoryGraphService.identity(RepositoryIdentity('owner/repo'), 'a' * 40)

    def identity(self, repository, revision):
        return RepositoryGraphService.identity(repository, revision)

    def get_status(self, identity):
        return GraphIndexStatus(identity, self.state, 6, 5)

    def query(self, identity, query):
        self.queries.append(query)
        if query.value not in {'B', 'src/b.py'}:
            return RepositoryGraphResult()
        nodes = [
            RepositoryGraphNode('a', 'symbol', 'A()', 'src/a.py', 'A()', 1),
            RepositoryGraphNode('b', 'symbol', 'B()', 'src/b.py', 'B()', 2),
            RepositoryGraphNode('c', 'symbol', 'C()', 'src/c.py', 'C()', 3),
            RepositoryGraphNode('d', 'symbol', 'D()', 'src/d.py', 'D()', 4),
        ]
        if self.unsafe:
            nodes.append(RepositoryGraphNode('evil', 'symbol', 'escape()', '../secret.py', 'escape()', 1))
        edges = [
            RepositoryGraphEdge('a', 'b', 'calls', 'EXTRACTED'),
            RepositoryGraphEdge('b', 'c', 'calls', 'EXTRACTED'),
            RepositoryGraphEdge('d', 'b', 'imports', 'INFERRED'),
        ]
        if self.duplicates:
            nodes.append(nodes[1])
            edges.extend([edges[0], RepositoryGraphEdge('a', 'b', 'calls', 'INFERRED')])
        return RepositoryGraphResult(nodes, edges)


class GraphContextTests(unittest.TestCase):
    def request(self, **kwargs):
        values = {
            'repository': 'owner/repo',
            'revision': 'a' * 40,
            'objective': 'Change the behavior of B and make sure callers remain compatible.',
            'explicit_symbols': ('B',),
        }
        values.update(kwargs)
        return GraphContextRequest(**values)

    def test_relevant_fixture_prioritizes_anchor_and_relationships(self):
        graph = FixtureGraph()
        outcome = GraphContextEnricher(graph).enrich(
            self.request(), index_id=graph.identity_value.index_id, graph_state='READY'
        )
        self.assertEqual(outcome.status, 'ready')
        briefing = outcome.briefing
        self.assertEqual(briefing.context.nodes[0].name, 'B()')
        self.assertEqual({node.name for node in briefing.context.nodes}, {'A()', 'B()', 'C()', 'D()'})
        self.assertEqual({edge.relationship for edge in briefing.context.relationships}, {'calls', 'imports'})
        self.assertIn('supporting evidence, not user instruction', briefing.rendered)
        self.assertIn('provenance: `INFERRED`', briefing.rendered)

    def test_exact_revision_and_lifecycle_are_required(self):
        graph = FixtureGraph()
        enricher = GraphContextEnricher(graph)
        for state, reason in [('STALE', 'index_stale'), ('FAILED', 'index_failed'), ('NOT_INDEXED', 'not_indexed')]:
            outcome = enricher.enrich(self.request(), index_id=graph.identity_value.index_id, graph_state=state)
            self.assertEqual((outcome.status, outcome.reason), ('skipped', reason))
        mismatch = enricher.enrich(self.request(), index_id='wrong', graph_state='READY')
        self.assertEqual(mismatch.reason, 'revision_mismatch')
        dirty = enricher.enrich(
            self.request(), index_id=graph.identity_value.index_id, graph_state='READY', worktree_dirty=True
        )
        self.assertEqual(dirty.reason, 'index_stale')

    def test_zero_match_skips_without_briefing(self):
        graph = FixtureGraph()
        outcome = GraphContextEnricher(graph).enrich(
            self.request(objective='ordinary prose', explicit_symbols=()),
            index_id=graph.identity_value.index_id,
            graph_state='READY',
        )
        self.assertEqual((outcome.status, outcome.reason, outcome.briefing), ('skipped', 'no_relevant_results', None))

    def test_limits_deduplicate_and_preserve_strongest_provenance(self):
        graph = FixtureGraph(duplicates=True)
        limits = GraphContextLimits(1, 3, 1, 2, 1, 700, 220)
        outcome = GraphContextEnricher(graph, limits).enrich(
            self.request(), index_id=graph.identity_value.index_id, graph_state='READY'
        )
        briefing = outcome.briefing
        self.assertLessEqual(len(briefing.context.nodes), 3)
        self.assertLessEqual(len(briefing.context.relationships), 1)
        self.assertLessEqual(len(briefing.context.relevant_files), 2)
        self.assertLessEqual(briefing.byte_count, 700)
        self.assertLessEqual(briefing.estimated_tokens, 220)
        self.assertEqual(len({node.id for node in briefing.context.nodes}), len(briefing.context.nodes))
        self.assertTrue(briefing.context.truncated)
        if briefing.context.relationships:
            self.assertEqual(briefing.context.relationships[0].confidence, 'EXTRACTED')

    def test_unsafe_paths_are_rejected(self):
        graph = FixtureGraph(unsafe=True)
        outcome = GraphContextEnricher(graph).enrich(
            self.request(), index_id=graph.identity_value.index_id, graph_state='READY'
        )
        self.assertNotIn('../secret.py', outcome.briefing.rendered)
        self.assertTrue(all(not path.startswith(('/', '..')) for path in outcome.briefing.context.relevant_files))

    def test_long_objective_still_enforces_query_cap(self):
        graph = FixtureGraph()
        outcome = GraphContextEnricher(graph, GraphContextLimits(max_queries=1)).enrich(
            self.request(objective=' '.join(f'NamedSymbol{i}' for i in range(100))),
            index_id=graph.identity_value.index_id,
            graph_state='READY',
        )
        self.assertLessEqual(len(graph.queries), 1)
        self.assertIn(outcome.status, {'ready', 'skipped'})

    def test_context_hash_changes_with_structural_briefing(self):
        graph = FixtureGraph()
        enricher = GraphContextEnricher(graph)
        by_symbol = enricher.enrich(
            self.request(explicit_paths=()), index_id=graph.identity_value.index_id, graph_state='READY'
        ).briefing
        by_path = enricher.enrich(
            self.request(explicit_symbols=(), explicit_paths=('src/b.py',)),
            index_id=graph.identity_value.index_id,
            graph_state='READY',
        ).briefing
        self.assertNotEqual(by_symbol.context_hash, by_path.context_hash)

    def test_single_node_and_oversized_summary_remain_bounded(self):
        graph = FixtureGraph()
        enricher = GraphContextEnricher(graph, GraphContextLimits(max_bytes=700, max_estimated_tokens=220))
        context = GraphContext(
            'owner/repo',
            'a' * 40,
            graph.identity_value.index_id,
            '2026-09-25T00:00:00+00:00',
            ('B',),
            (RepositoryGraphNode('b', 'symbol', 'B()', 'src/b.py', 'B()', 2),),
            (),
            ('src/b.py',),
            {'untrusted_summary': 'x' * 100000},
            query_count=1,
        )
        briefing = enricher.render(context)
        self.assertIn('B()', briefing.rendered)
        self.assertNotIn('untrusted_summary', briefing.rendered)
        self.assertLessEqual(briefing.byte_count, 700)
        self.assertLessEqual(briefing.estimated_tokens, 220)


class PlandexStructuralContextTests(unittest.TestCase):
    def test_native_load_is_named_verified_and_idempotent(self):
        adapter = PlandexExecutor(require_tokenizer=False)
        calls, contexts = [], []

        def run(task_id, args, cwd, stdin_data=None):
            calls.append((args, stdin_data))
            if args == ['ls', '--json']:
                return json.dumps(contexts)
            self.assertEqual(args[:2], ['load', '--name'])
            self.assertNotIn('.graphify-context', cwd)
            contexts.append({'id': 'native-context-id', 'name': args[2], 'type': 'piped data'})
            return ''

        adapter._run = run
        first = adapter.load_structural_context('task', '/repo', 'bounded evidence', 'a' * 64)
        second = adapter.load_structural_context('task', '/repo', 'bounded evidence', 'a' * 64)
        self.assertEqual(first.status, 'loaded')
        self.assertEqual(second.status, 'already_loaded')
        self.assertEqual(sum(args[0] == 'load' for args, _ in calls), 1)
        self.assertEqual([data for args, data in calls if args[0] == 'load'], ['bounded evidence'])

    def test_adapter_rejects_oversized_context(self):
        adapter = PlandexExecutor(require_tokenizer=False)
        with mock.patch.dict(os.environ, {'GRAPH_CONTEXT_MAX_BYTES': '10'}):
            with self.assertRaisesRegex(RuntimeError, 'plandex_context_invalid'):
                adapter.load_structural_context('task', '/repo', 'too much structural context', 'a' * 64)

    def test_service_load_failure_is_nonfatal_and_events_are_bounded(self):
        graph = FixtureGraph()

        class Plandex:
            def load_structural_context(self, *args):
                raise RuntimeError('secret raw failure')

        store = MemoryTaskStore()
        task = CodingTask('owner/repo', 'user_prompt', 'main', user_prompt='Change B')
        task.base_commit_sha = 'a' * 40
        task.worktree_path = '/tmp/not-exposed'
        task.graph_index_id = graph.identity_value.index_id
        task.graph_status = 'READY'
        task.graph_available = True
        store.create(task)
        service = TaskService(store, object(), Plandex(), graph, GraphContextEnricher(graph))
        graph.worktree_is_stale = lambda *args: False
        result = service.enrich_plan_context(task.task_id, TaskInput('Change B', 'Change B'))
        self.assertEqual(result.graph_context_status, 'load_failed')
        self.assertNotEqual(result.state.value, 'FAILED')
        persisted = str(result.to_dict()) + str([event.to_dict() for event in store.events(task.task_id)])
        self.assertNotIn('secret raw failure', persisted)
        self.assertNotIn('# Repository Structural Context', persisted)
        self.assertNotIn('/tmp/not-exposed', str([event.to_dict() for event in store.events(task.task_id)]))

    def test_service_persists_metadata_and_skips_identical_reload(self):
        graph = FixtureGraph()

        class Plandex:
            def __init__(self):
                self.loads = []

            def load_structural_context(self, task_id, cwd, context, context_hash):
                self.loads.append((context, context_hash))
                return PlandexContextLoadResult('loaded', f'structural-context-{context_hash[:16]}', 'native-id')

        store, plandex = MemoryTaskStore(), Plandex()
        task = CodingTask('owner/repo', 'user_prompt', 'main', user_prompt='Change B')
        task.base_commit_sha = 'a' * 40
        task.worktree_path = '/private/worktree'
        task.graph_index_id = graph.identity_value.index_id
        task.graph_status = 'READY'
        task.graph_available = True
        store.create(task)
        graph.worktree_is_stale = lambda *args: False
        service = TaskService(store, object(), plandex, graph, GraphContextEnricher(graph))
        with mock.patch.dict(os.environ, {'SPARK_API_KEY': 'STRUCTURAL_SECRET_123'}):
            first = service.enrich_plan_context(task.task_id, TaskInput('Change B', 'Change B STRUCTURAL_SECRET_123'))
            second = service.enrich_plan_context(task.task_id, TaskInput('Change B', 'Change B STRUCTURAL_SECRET_123'))
        self.assertEqual(len(plandex.loads), 1)
        self.assertEqual(second.graph_context_status, 'already_loaded')
        self.assertEqual(first.graph_context_hash, second.graph_context_hash)
        self.assertGreater(first.graph_context_bytes, 0)
        self.assertGreater(first.graph_context_nodes, 0)
        self.assertNotIn('STRUCTURAL_SECRET_123', plandex.loads[0][0])
        event_dump = str([event.to_dict() for event in store.events(task.task_id)])
        self.assertNotIn(plandex.loads[0][0], event_dump)
        self.assertNotIn('/private/worktree', event_dump)


if __name__ == '__main__':
    unittest.main()

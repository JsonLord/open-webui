import concurrent.futures
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.control_plane.git import PreparedRepository  # noqa: E402
from open_webui.control_plane.graph import (  # noqa: E402
    GRAPHIFY_VERSION,
    GraphIndexState,
    GraphQueryKind,
    RepositoryGraphError,
    RepositoryGraphQuery,
    RepositoryGraphService,
    RepositoryIdentity,
)
from open_webui.control_plane.repositories import RepositoryAllowlist  # noqa: E402
from open_webui.control_plane.service import TaskService  # noqa: E402
from open_webui.control_plane.storage import MemoryTaskStore  # noqa: E402

FAKE_GRAPHIFY = r"""#!/usr/bin/env python3
import json, os, pathlib, sys, time
if os.environ.get('GITHUB_PAT') or os.environ.get('SPARK_API_KEY') or os.environ.get('PLANDEX_API_KEY'):
    raise SystemExit(91)
if os.environ.get('GRAPHIFY_QUERY_LOG_DISABLE') != '1':
    raise SystemExit(92)
if pathlib.Path(sys.argv[0]).name.startswith('fail-'):
    raise SystemExit(2)
source = pathlib.Path(sys.argv[2])
out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1]) / 'graphify-out'
out.mkdir(parents=True)
counter = pathlib.Path(sys.argv[0] + '.count')
with counter.open('a') as f: f.write('1\n')
nodes = [
 {'id':'app','label':'app.py','file_type':'code','source_file':'app.py','source_location':'L1','_origin':'ast'},
 {'id':'main','label':'main()','file_type':'code','source_file':'app.py','source_location':'L1','_callable':True,'_origin':'ast'},
 {'id':'helper','label':'helper()','file_type':'code','source_file':'lib.py','source_location':'L2','_callable':True,'_origin':'ast'},
]
edges = [
 {'source':'app','target':'main','relation':'contains','confidence':'EXTRACTED'},
 {'source':'main','target':'helper','relation':'calls','confidence':'EXTRACTED'},
]
(out/'graph.json').write_text(json.dumps({'nodes':nodes,'edges':edges,'hyperedges':[]}))
"""


class RepositoryGraphServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        (self.repo / 'app.py').write_text('from lib import helper\ndef main(): return helper()\n')
        (self.repo / 'lib.py').write_text('def helper(): return 1\n')
        subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=self.repo, check=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.invalid'], cwd=self.repo, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=self.repo, check=True)
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-qm', 'fixture'], cwd=self.repo, check=True)
        self.revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.repo, text=True).strip()
        self.executable = self.root / 'graphify'
        self.executable.write_text(FAKE_GRAPHIFY)
        self.executable.chmod(0o755)
        self.storage = self.root / 'persistent' / 'graphs'
        self.service = RepositoryGraphService(self.storage, str(self.executable), timeout=5)
        self.repository = RepositoryIdentity('owner/repo')

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_index_is_ready_and_queries_are_normalized(self):
        identity = self.service.ensure_index(self.repository, self.repo, self.revision)
        status = self.service.get_status(identity)
        self.assertEqual(status.state, GraphIndexState.READY)
        self.assertEqual((status.node_count, status.edge_count), (3, 2))
        result = self.service.query(identity, RepositoryGraphQuery(GraphQueryKind.NEIGHBORHOOD, 'main'))
        self.assertEqual({node.name for node in result.nodes}, {'main()', 'helper()', 'app.py'})
        self.assertEqual({edge.relationship for edge in result.edges}, {'contains', 'calls'})
        self.assertTrue(all(not (node.path or '').startswith('/') for node in result.nodes))

    def test_existing_index_is_reused_and_survives_service_restart(self):
        identity = self.service.ensure_index(self.repository, self.repo, self.revision)
        self.service.ensure_index(self.repository, self.repo, self.revision)
        self.assertEqual(Path(str(self.executable) + '.count').read_text().splitlines(), ['1'])
        restarted = RepositoryGraphService(self.storage, str(self.executable))
        self.assertEqual(restarted.get_status(identity).state, GraphIndexState.READY)
        self.assertEqual(restarted.query(identity, RepositoryGraphQuery(GraphQueryKind.SUMMARY)).summary['nodes'], 3)

    def test_new_commit_has_distinct_identity(self):
        first = self.service.ensure_index(self.repository, self.repo, self.revision)
        (self.repo / 'new.py').write_text('VALUE = 1\n')
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-qm', 'second'], cwd=self.repo, check=True)
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.repo, text=True).strip()
        second = self.service.ensure_index(self.repository, self.repo, revision)
        self.assertNotEqual(first.index_id, second.index_id)
        self.assertNotEqual(self.service._index_dir(first), self.service._index_dir(second))

    def test_concurrent_ensure_builds_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            identities = list(
                pool.map(
                    lambda _: self.service.ensure_index(self.repository, self.repo, self.revision),
                    range(2),
                )
            )
        self.assertEqual(identities[0], identities[1])
        self.assertEqual(Path(str(self.executable) + '.count').read_text().splitlines(), ['1'])

    def test_failed_index_is_persisted_without_source_damage(self):
        before = (self.repo / 'app.py').read_text()
        failing = self.root / 'fail-graphify'
        failing.write_text(FAKE_GRAPHIFY)
        failing.chmod(0o755)
        service = RepositoryGraphService(self.storage, str(failing))
        with self.assertRaisesRegex(RepositoryGraphError, 'graphify_index_failed'):
            service.ensure_index(self.repository, self.repo, self.revision)
        identity = self.service.identity(self.repository, self.revision)
        self.assertEqual(self.service.get_status(identity).state, GraphIndexState.FAILED)
        self.assertEqual((self.repo / 'app.py').read_text(), before)

    def test_dirty_worktree_marks_base_graph_stale(self):
        self.service.ensure_index(self.repository, self.repo, self.revision)
        self.assertFalse(self.service.worktree_is_stale(self.repo, self.revision))
        (self.repo / 'app.py').write_text('changed = True\n')
        self.assertTrue(self.service.worktree_is_stale(self.repo, self.revision))

    def test_revision_mismatch_fails_closed(self):
        with self.assertRaisesRegex(RepositoryGraphError, 'graph_revision_mismatch'):
            self.service.ensure_index(self.repository, self.repo, 'a' * 40)

    def test_identity_includes_repository_revision_and_versions(self):
        identity = self.service.identity(self.repository, self.revision)
        self.assertEqual(identity.graphify_version, GRAPHIFY_VERSION)
        self.assertNotEqual(identity, self.service.identity(RepositoryIdentity('owner/other'), self.revision))

    def test_graphify_child_does_not_receive_credentials(self):
        with mock.patch.dict(
            os.environ,
            {'GITHUB_PAT': 'secret', 'SPARK_API_KEY': 'secret', 'PLANDEX_API_KEY': 'secret'},
        ):
            self.service.ensure_index(self.repository, self.repo, self.revision)

    def test_repository_preparation_persists_graph_state_and_event(self):
        class Worktrees:
            allowlist = RepositoryAllowlist(frozenset({'owner/repo'}), frozenset())

            def prepare(inner_self, repository, base_branch, task_id, slug):
                return PreparedRepository(repository, 'agent/test', self.repo, self.revision)

        store = MemoryTaskStore()
        service = TaskService(store, Worktrees(), object(), self.service)
        task = service.create_task(repository='owner/repo', prompt='inspect')
        result = service.prepare_repository(task.task_id)
        self.assertTrue(result.graph_available)
        self.assertEqual(result.graph_status, GraphIndexState.READY)
        self.assertEqual(result.graph_revision, self.revision)
        self.assertIn('graph_index_ready', [event.type for event in store.events(task.task_id)])

    def test_graph_failure_does_not_fail_repository_task(self):
        class Worktrees:
            allowlist = RepositoryAllowlist(frozenset({'owner/repo'}), frozenset())

            def prepare(inner_self, repository, base_branch, task_id, slug):
                return PreparedRepository(repository, 'agent/test', self.repo, self.revision)

        failing = self.root / 'fail-graphify'
        failing.write_text(FAKE_GRAPHIFY)
        failing.chmod(0o755)
        store = MemoryTaskStore()
        service = TaskService(
            store,
            Worktrees(),
            object(),
            RepositoryGraphService(self.storage, str(failing)),
        )
        task = service.create_task(repository='owner/repo', prompt='inspect')
        result = service.prepare_repository(task.task_id)
        self.assertEqual(result.state.value, 'PREPARING_REPO')
        self.assertEqual(result.graph_status, GraphIndexState.FAILED)
        self.assertFalse(result.graph_available)
        self.assertIn('graph_index_failed', [event.type for event in store.events(task.task_id)])


if __name__ == '__main__':
    unittest.main()

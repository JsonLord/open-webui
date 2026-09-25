import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.control_plane.adapters import (  # noqa: E402
    GitHubMCP,
    PlandexExecutor,
    PlandexPlanIdentity,
    normalize_issue,
    redact,
)
from open_webui.control_plane.domain import CodingTask, InvalidTransition, TaskState  # noqa: E402
from open_webui.control_plane.git import GitWorktrees  # noqa: E402
from open_webui.control_plane.health import aggregate_health  # noqa: E402
from open_webui.control_plane.repositories import (  # noqa: E402
    RepositoryAllowlist,
    contained_path,
    normalize_repository,
    task_branch,
)
from open_webui.control_plane.service import ControlPlaneError, TaskService  # noqa: E402
from open_webui.control_plane.storage import MemoryTaskStore, metadata  # noqa: E402


class FakePlandex(PlandexExecutor):
    def __init__(self):
        self.cancelled = []

    def cancel(self, task_id):
        self.cancelled.append(task_id)
        return True

    def create_plan(self, task_id, cwd):
        return PlandexPlanIdentity('plan-123', 'agent-test', 'project-123', 'main')


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.allowed = RepositoryAllowlist(frozenset({'owner/repo'}), frozenset())
        self.store, self.plandex = MemoryTaskStore(), FakePlandex()
        self.git = GitWorktrees(root / 'cache', root / 'worktrees', self.allowed)
        self.service = TaskService(self.store, self.git, self.plandex)

    def tearDown(self):
        self.temp.cleanup()

    def test_task_creation_and_event_order(self):
        task = self.service.create_task(repository='OWNER/repo', prompt='Fix it')
        self.assertEqual(task.state, TaskState.QUEUED)
        self.service.transition(task.task_id, TaskState.PREPARING_REPO, 'start')
        self.assertEqual([e.sequence for e in self.store.events(task.task_id)], [1, 2])

    def test_invalid_transition(self):
        task = CodingTask('owner/repo', 'user_prompt', 'main', user_prompt='x')
        with self.assertRaises(InvalidTransition):
            task.transition(TaskState.COMPLETED)

    def test_incomplete_task_recovery_fails_closed(self):
        task = self.service.create_task(repository='owner/repo', prompt='a')
        self.service.transition(task.task_id, TaskState.PREPARING_REPO, 'start')
        self.assertEqual(self.store.recover_incomplete(), [task.task_id])
        recovered = self.store.get(task.task_id)
        self.assertEqual(recovered.state, TaskState.FAILED)
        self.assertEqual(recovered.last_error, 'recovery_required')

    def test_idempotent_creation(self):
        one = self.service.create_task(repository='owner/repo', prompt='a', idempotency_key='same')
        two = self.service.create_task(repository='owner/repo', prompt='b', idempotency_key='same')
        self.assertEqual(one.task_id, two.task_id)
        self.assertEqual(len(self.store.list()), 1)

    def test_cancel_preserves_task_and_only_owned_process(self):
        task = self.service.create_task(repository='owner/repo', prompt='a')
        result = self.service.cancel(task.task_id)
        self.assertEqual(result.state, TaskState.CANCELLED)
        self.assertEqual(self.plandex.cancelled, [task.task_id])

    def test_real_owned_child_cancellation_does_not_kill_unrelated_process(self):
        executable = Path(self.temp.name) / 'blocking-plandex'
        owned_pid_file = Path(self.temp.name) / 'owned.pid'
        executable.write_text(f'#!/bin/sh\nsleep 60 &\necho $! > {owned_pid_file}\nwait\n')
        executable.chmod(0o755)
        adapter = PlandexExecutor(executable=str(executable), timeout=120)
        unrelated = subprocess.Popen(['sleep', '60'])
        errors = []

        def run():
            try:
                adapter.status('owned-task', self.temp.name)
            except RuntimeError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=run)
        thread.start()
        for _ in range(100):
            if 'owned-task' in adapter._processes and owned_pid_file.exists():
                break
            time.sleep(0.01)
        self.assertTrue(adapter.cancel('owned-task'))
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(unrelated.poll())
        owned_pid = int(owned_pid_file.read_text())
        for _ in range(100):
            status = Path(f'/proc/{owned_pid}/status')
            if not status.exists() or '\nState:\tZ' in status.read_text():
                break
            time.sleep(0.01)
        status = Path(f'/proc/{owned_pid}/status')
        self.assertTrue(not status.exists() or '\nState:\tZ' in status.read_text())
        unrelated.terminate()
        unrelated.wait(timeout=5)
        self.assertTrue(errors)

    def test_task_requires_exactly_one_source(self):
        with self.assertRaises(ControlPlaneError):
            self.service.create_task(repository='owner/repo')
        with self.assertRaises(ControlPlaneError):
            self.service.create_task(repository='owner/repo', issue_number=1, prompt='x')

    def test_repository_normalization_and_allowlist(self):
        self.assertEqual(normalize_repository('https://github.com/Owner/Repo.git'), 'owner/repo')
        with self.assertRaises(ValueError):
            normalize_repository('https://evil.example/owner/repo')
        with self.assertRaises(ValueError):
            normalize_repository('https://user:secret@github.com/owner/repo')
        with self.assertRaises(PermissionError):
            self.allowed.authorize('owner/other')

    def test_branch_and_containment(self):
        self.assertEqual(task_branch('Fix Bug!', 'abcdef12-1234'), 'agent/fix-bug-abcdef12')
        root = Path(self.temp.name) / 'root'
        root.mkdir()
        self.assertTrue(str(contained_path(root, 'task')).startswith(str(root)))
        with self.assertRaises(ValueError):
            contained_path(root, '../escape')

    def test_secret_redaction(self):
        self.assertEqual(redact('token SECRET', ('SECRET',)), 'token [REDACTED]')

    def test_plandex_bearer_redaction(self):
        self.assertEqual(
            redact('Authorization: Bearer native-secret'),
            'Authorization: Bearer [REDACTED]',
        )
        self.assertEqual(redact('{"token":"native-secret"}'), '{"token":"[REDACTED]"}')

    def test_github_secret_is_not_forwarded_or_persisted(self):
        secret = 'PHASE3D_FAKE_TOKEN_DO_NOT_LEAK_9f3a'
        executable = Path(self.temp.name) / 'print-env'
        executable.write_text(
            '#!/bin/sh\nprintf "%s|%s|%s" "$GITHUB_PAT" "$GH_TOKEN" "$GITHUB_PERSONAL_ACCESS_TOKEN"\n'
        )
        executable.chmod(0o755)
        adapter = PlandexExecutor(executable=str(executable))
        with mock.patch.dict(
            os.environ,
            {'GITHUB_PAT': secret, 'GH_TOKEN': secret, 'GITHUB_PERSONAL_ACCESS_TOKEN': secret},
            clear=False,
        ):
            self.assertEqual(adapter.status('task', self.temp.name), '||')
            with mock.patch('open_webui.control_plane.git.subprocess.run') as run:
                run.return_value = mock.Mock(returncode=0, stdout='', stderr='')
                self.git._run(['status'])
                child_env = run.call_args.kwargs['env']
        self.assertNotIn('GITHUB_PAT', child_env)
        self.assertNotIn('GH_TOKEN', child_env)
        self.assertNotIn('GITHUB_PERSONAL_ACCESS_TOKEN', child_env)
        task = self.service.create_task(repository='owner/repo', prompt='ordinary request')
        persisted = str(task.to_dict()) + str([event.to_dict() for event in self.store.events(task.task_id)])
        self.assertNotIn(secret, persisted)

    def test_privacy_safe_routing_event_is_bounded(self):
        task = self.service.create_task(repository='owner/repo', prompt='secret prompt')
        event = self.service.record_routing(
            task.task_id,
            candidate_tool_ids=['repo.read'],
            selected_tool='repo.read',
            confidence=0.9,
            outcome='selected',
            policy_outcome='allowed',
        )
        self.assertNotIn('secret prompt', str(event.to_dict()))
        with self.assertRaises(ValueError):
            self.service.record_routing(
                task.task_id,
                candidate_tool_ids=[str(i) for i in range(6)],
                selected_tool=None,
                confidence=None,
                outcome='abstain',
                policy_outcome='none',
            )

    def test_issue_normalization_is_bounded(self):
        issue = normalize_issue(
            {'number': 2, 'title': 'T', 'body': 'x' * 13000, 'html_url': 'u', 'labels': [{'name': 'bug'}]}
        )
        self.assertEqual(len(issue.objective), 12000)
        self.assertEqual(issue.labels, ('bug',))

    def test_github_mcp_issue_response_is_structurally_parsed(self):
        client = GitHubMCP()
        client.call_tool = lambda name, arguments: {
            'content': [
                {
                    'type': 'text',
                    'text': '{"number":7,"title":"Bug","body":"Fix","html_url":"https://github.com/o/r/issues/7"}',
                }
            ]
        }
        issue = client.read_issue('o/r', 7)
        self.assertEqual(issue['number'], 7)

    def test_plandex_new_uses_stable_name_and_does_not_fake_plan_id(self):
        adapter = PlandexExecutor()
        calls = []
        current_json = '{"planId":"p1","planName":"agent-abcdef12","projectId":"pr1","branch":"main"}'
        adapter._run = lambda task_id, args, cwd: (
            calls.append((task_id, args, cwd)) or (current_json if args[0] == 'current' else '')
        )
        identity = adapter.create_plan('abcdef12-rest', self.temp.name)
        self.assertEqual(identity.plan_id, 'p1')
        self.assertEqual(identity.plan_name, 'agent-abcdef12')
        self.assertEqual(calls[0][1], ['new', '--name', 'agent-abcdef12', '--context-dir', '.'])
        self.assertEqual(calls[1][1], ['current', '--json'])

    def test_issue_to_native_plan_flow_with_normalized_context(self):
        task = self.service.create_task(repository='owner/repo', issue_number=7)
        self.service.prepare_repository = lambda task_id, slug='task': self._fake_prepared(task_id)
        result, task_input = self.service.prepare_and_create_plan(
            task.task_id,
            issue_loader=lambda repo, number: {
                'number': number,
                'title': 'Bug',
                'body': 'Fix safely',
                'html_url': 'https://github.com/owner/repo/issues/7',
            },
        )
        self.assertEqual(result.plan_id, 'plan-123')
        self.assertEqual(result.plan_name, 'agent-test')
        self.assertTrue(result.plandex_current_verified)
        self.assertEqual(task_input.issue_number, 7)
        self.assertEqual(result.state, TaskState.PLANNING)

    def _fake_prepared(self, task_id):
        self.service.transition(task_id, TaskState.PREPARING_REPO, 'start')
        task = self.service.require(task_id)
        task.worktree_path = self.temp.name
        self.store.save(task)
        return task

    def test_health_has_distinct_dimensions(self):
        health = aggregate_health({'postgresql': {'reachable': True, 'runtime_verified': True}})
        self.assertFalse(health['ok'])
        self.assertIn('configured', health['components']['github_mcp'])
        self.assertIn('reachable', health['components']['spark_contract'])
        self.assertIn('storage_available', health['components']['graphify'])
        self.assertNotIn('path', health['components']['graphify'])

    def test_postgres_schema_has_durable_domain_tables(self):
        self.assertEqual(metadata.schema, 'control_plane')
        self.assertEqual(
            set(metadata.tables),
            {'control_plane.tasks', 'control_plane.task_events', 'control_plane.repositories'},
        )

    def test_plandex_failure_is_normalized(self):
        adapter = PlandexExecutor(executable='definitely-missing-plandex')
        with self.assertRaisesRegex(RuntimeError, 'plandex_unavailable'):
            adapter.create_plan('id', self.temp.name)


class GitIntegrationTests(unittest.TestCase):
    def run_git(self, cwd, *args):
        return subprocess.run(['git', *args], cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()

    def test_real_git_worktree_and_idempotency(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            origin = root / 'origin.git'
            seed = root / 'seed'
            self.run_git(root, 'init', '--bare', str(origin))
            self.run_git(root, 'clone', str(origin), str(seed))
            self.run_git(seed, 'config', 'user.email', 'test@example.com')
            self.run_git(seed, 'config', 'user.name', 'Test')
            (seed / 'README').write_text('hello\n')
            self.run_git(seed, 'add', 'README')
            self.run_git(seed, 'commit', '-m', 'init')
            self.run_git(seed, 'branch', '-M', 'main')
            self.run_git(seed, 'push', 'origin', 'main')
            manager = GitWorktrees(
                root / 'cache', root / 'worktrees', RepositoryAllowlist(frozenset({'owner/repo'}), frozenset())
            )
            # Seed the deterministic bare cache without mocking Git.
            cache = root / 'cache' / 'owner' / 'repo.git'
            cache.parent.mkdir(parents=True)
            self.run_git(root, 'clone', '--bare', str(origin), str(cache))
            self.run_git(cache, 'remote', 'set-url', 'origin', str(origin))
            prepared = manager.prepare('owner/repo', 'main', 'abcdef12-1234', 'fix')
            again = manager.prepare('owner/repo', 'main', 'abcdef12-1234', 'fix')
            self.assertEqual(prepared.worktree, again.worktree)
            self.assertTrue((prepared.worktree / 'README').exists())


if __name__ == '__main__':
    unittest.main()

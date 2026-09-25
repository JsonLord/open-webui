"""Opt-in tests against the production PostgreSQL task store.

Run with CONTROL_PLANE_TEST_DATABASE_URL set to a disposable PostgreSQL database.
"""

import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.control_plane.domain import CodingTask, TaskState  # noqa: E402
from open_webui.control_plane.storage import PostgresTaskStore  # noqa: E402

DATABASE_URL = os.getenv('CONTROL_PLANE_TEST_DATABASE_URL')


@unittest.skipUnless(DATABASE_URL, 'CONTROL_PLANE_TEST_DATABASE_URL is not configured')
class PostgresRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(DATABASE_URL, pool_size=12, max_overflow=4)
        with cls.engine.begin() as connection:
            connection.exec_driver_sql('DROP SCHEMA IF EXISTS control_plane CASCADE')
        cls.store = PostgresTaskStore(cls.engine)
        cls.store.create_schema()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql('TRUNCATE control_plane.task_events, control_plane.tasks CASCADE')
            connection.exec_driver_sql('TRUNCATE control_plane.repositories')

    def test_restart_persistence_and_recovery(self):
        task = self.store.create(CodingTask('owner/repo', 'user_prompt', 'main', user_prompt='runtime'))
        self.store.transition(task.task_id, TaskState.PREPARING_REPO, 'Preparing')
        self.store.register_repository('owner/repo', {'source': 'runtime'})

        restarted = PostgresTaskStore(create_engine(DATABASE_URL))
        self.assertEqual(restarted.get(task.task_id).state, TaskState.PREPARING_REPO)
        self.assertEqual([event.sequence for event in restarted.events(task.task_id)], [1, 2])
        self.assertEqual(restarted.list_repositories(), ['owner/repo'])
        self.assertEqual(restarted.recover_incomplete(), [task.task_id])
        recovered = restarted.get(task.task_id)
        self.assertEqual(recovered.state, TaskState.FAILED)
        self.assertEqual(recovered.last_error, 'recovery_required')
        self.assertEqual([event.sequence for event in restarted.events(task.task_id)], [1, 2, 3])
        restarted.engine.dispose()

    def test_concurrent_event_sequences_are_unique_and_ordered(self):
        task = self.store.create(CodingTask('owner/repo', 'user_prompt', 'main', user_prompt='runtime'))
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda number: self.store.append_event(task.task_id, 'warning', str(number)), range(24)))
        sequences = [event.sequence for event in self.store.events(task.task_id)]
        self.assertEqual(sequences, list(range(1, 26)))

    def test_concurrent_idempotency_key_creates_one_task_and_initial_event(self):
        def create(number):
            return self.store.create(
                CodingTask(
                    'owner/repo',
                    'user_prompt',
                    'main',
                    user_prompt=f'runtime-{number}',
                    idempotency_key='same-runtime-key',
                )
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(create, range(16)))
        self.assertEqual(len({task.task_id for task in results}), 1)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(len(self.store.events(results[0].task_id)), 1)


if __name__ == '__main__':
    unittest.main()

import json
import unittest
from unittest import mock

from open_webui.control_plane.adapters import JITPlanner, PlandexExecutor
from open_webui.control_plane.domain import (
    AutonomyLevel,
    CheckpointType,
    JITContextPolicy,
    JITDecision,
    JITExecutionPolicy,
    JITScopePolicy,
    JITTaskContext,
    JITValidationPolicy,
    TaskClass,
)


class JITPolicyTests(unittest.TestCase):
    def setUp(self):
        self.context = JITTaskContext(
            task_id='12345678-1234-1234-1234-123456789abc',
            repository='open-webui/open-webui',
            base_revision='a' * 40,
            objective='Fix memory leak in websocket reconnection handler',
            explicit_paths=('backend/open_webui/socket/main.py',),
        )
        self.planner = JITPlanner()

    def test_fallback_decision_when_unreachable(self):
        decision = self.planner.plan_initial(self.context)
        self.assertEqual(decision.decision_id, 'fallback-12345678')
        self.assertEqual(decision.task_class, TaskClass.UNKNOWN)
        self.assertEqual(decision.execution_policy.max_replans, 0)
        self.assertIn('conservative Plandex defaults', decision.plan_seed)

    def test_markdown_code_block_json_parsing(self):
        markdown_text = "```json\n{\"decision_id\": \"jit-1\", \"task_class\": \"bug_fix\"}\n```"
        extracted = self.planner._extract_json(markdown_text)
        self.assertEqual(extracted['decision_id'], 'jit-1')
        self.assertEqual(extracted['task_class'], 'bug_fix')

    def test_checkpoint_evaluation_and_replan(self):
        eval_cont = self.planner.evaluate_checkpoint(self.context, CheckpointType.PLAN_READY, {})
        self.assertEqual(eval_cont, 'CONTINUE')

        eval_replan = self.planner.evaluate_checkpoint(self.context, CheckpointType.SCOPE_CHANGED, {})
        self.assertEqual(eval_replan, 'REPLAN')

        fallback = self.planner.fallback_decision(self.context)
        fallback_replan = self.planner.replan(self.context, fallback, 'scope_expanded')
        self.assertTrue(fallback_replan.decision_id.startswith('fallback-'))

        mock_planner = JITPlanner(base_url='http://127.0.0.1:9999', api_key='secret')
        mock_replan = mock_planner.replan(self.context, fallback, 'scope_expanded')
        self.assertTrue(mock_replan.decision_id.startswith('replan-'))

    def test_schema_validation_and_budget_clamping(self):
        raw_json = {
            'decision_id': 'jit-custom-1',
            'task_class': 'bug_fix',
            'scope': {'expected_files': ['backend/open_webui/socket/main.py'], 'risk': 'medium'},
            'execution_policy': {
                'autonomy': 'HIGH',
                'max_iterations': 99,  # Should be clamped to 8
                'max_replans': 10,     # Should be clamped to 3
                'max_validation_cycles': 5, # Should be clamped to 3
                'max_tool_failures': 8,     # Should be clamped to 4
            },
            'plan_seed': 'Investigate websocket ping/pong intervals.',
            'rationale_summary': 'Fix connection leak.',
        }
        parsed = self.planner.parse_json_decision(raw_json, self.context.task_id)
        clamped = self.planner.clamp_decision(parsed)

        self.assertEqual(clamped.task_class, TaskClass.BUG_FIX)
        self.assertEqual(clamped.execution_policy.autonomy, AutonomyLevel.HIGH)
        self.assertEqual(clamped.execution_policy.max_iterations, 8)
        self.assertEqual(clamped.execution_policy.max_replans, 3)
        self.assertEqual(clamped.execution_policy.max_validation_cycles, 3)
        self.assertEqual(clamped.execution_policy.max_tool_failures, 4)

    def test_malformed_json_triggers_fallback(self):
        with self.assertRaises(ValueError):
            self.planner.parse_json_decision({'task_class': 'invalid_class_value'}, self.context.task_id)

    def test_jit_strategy_plandex_named_load(self):
        adapter = PlandexExecutor(require_tokenizer=False)
        calls, contexts = [], []

        def run(task_id, args, cwd, stdin_data=None):
            calls.append((args, stdin_data))
            if args == ['ls', '--json']:
                return json.dumps(contexts)
            if args[0] == 'rm':
                return ''
            self.assertEqual(args[:2], ['load', '--name'])
            contexts.append({'id': 'jit-context-id', 'name': args[2], 'type': 'piped data'})
            return ''

        adapter._run = run
        res = adapter.load_jit_strategy('task-123', '/tmp/repo', 'Strategy text', 'a' * 64)
        self.assertEqual(res.status, 'loaded')
        self.assertTrue(res.name.startswith('jit-strategy-'))


if __name__ == '__main__':
    unittest.main()

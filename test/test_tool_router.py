import unittest
from unittest import mock

from integration.tool_router import (
    NeedleBackend,
    RouterError,
    RouterSettings,
    ToolDecision,
    ToolGateway,
    ToolRouter,
    ToolSpec,
)

WEATHER = ToolSpec(
    name="get_weather",
    description="Read weather for a city.",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string", "minLength": 1}},
        "required": ["city"],
        "additionalProperties": False,
    },
)

WRITE_FILE = ToolSpec(
    name="write_file",
    description="Write text to a workspace file.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
        "additionalProperties": False,
    },
    effect="write",
)


class FakeBackend:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def select(self, intent, tools, max_new_tokens):
        self.requests.append((intent, tools, max_new_tokens))
        return self.response


def response(calls, confidence=0.9):
    return {
        "type": "call",
        "function_calls": calls,
        "confidence": confidence,
        "reasoning": "test reasoning",
    }


class ToolRouterTests(unittest.TestCase):
    def route(self, model_response, tools=(WEATHER,), settings=None, role="agent"):
        backend = FakeBackend(model_response)
        decision = ToolRouter(backend, settings or RouterSettings()).route("weather in Lagos", tools, role=role)
        return decision, backend

    def test_high_confidence_valid_call_is_selected(self):
        decision, backend = self.route(response([{"name": "get_weather", "arguments": {"city": "Lagos"}}]))
        self.assertEqual(decision.status, "selected")
        self.assertEqual(decision.arguments, {"city": "Lagos"})
        self.assertEqual(len(backend.requests[0][1]), 1)

    def test_empty_calls_abstain(self):
        decision, _ = self.route(response([], confidence=0.8))
        self.assertEqual(decision.status, "abstain")

    def test_inference_failure_is_rejected(self):
        decision, _ = self.route(
            {
                "type": "respond",
                "success": False,
                "error": "decode failed",
                "function_calls": [],
            }
        )
        self.assertEqual(decision.status, "rejected")
        self.assertIn("decode failed", decision.reason)

    def test_calls_require_call_response_type(self):
        with self.assertRaisesRegex(RouterError, "non-call response type"):
            self.route(
                {
                    "type": "respond",
                    "function_calls": [{"name": "get_weather", "arguments": {"city": "Lagos"}}],
                    "confidence": 0.9,
                }
            )

    def test_low_confidence_abstains(self):
        decision, _ = self.route(response([{"name": "get_weather", "arguments": {"city": "Lagos"}}], 0.2))
        self.assertEqual(decision.status, "abstain")

    def test_middle_confidence_requires_confirmation(self):
        decision, _ = self.route(response([{"name": "get_weather", "arguments": {"city": "Lagos"}}], 0.5))
        self.assertEqual(decision.status, "confirm")

    def test_missing_confidence_abstains(self):
        decision, _ = self.route(response([{"name": "get_weather", "arguments": {"city": "Lagos"}}], None))
        self.assertEqual(decision.status, "abstain")

    def test_suppressed_call_requires_confirmation(self):
        decision, _ = self.route(
            {
                "type": "call",
                "function_calls": [],
                "suppressed_calls": [{"name": "get_weather", "arguments": {"city": "Lagos"}}],
                "confidence": 0.1,
            }
        )
        self.assertEqual(decision.status, "confirm")
        self.assertEqual(decision.tool, "get_weather")

    def test_ungrounded_call_is_rejected_even_with_high_confidence(self):
        decision, _ = self.route(
            {
                **response([{"name": "get_weather", "arguments": {"city": "Lagos"}}]),
                "validation": {"ungrounded": ["get_weather.city"]},
            }
        )
        self.assertEqual(decision.status, "rejected")
        self.assertEqual(decision.model_validation["ungrounded"], ["get_weather.city"])

    def test_negated_call_is_rejected(self):
        decision, _ = self.route(
            {
                **response([{"name": "get_weather", "arguments": {"city": "Lagos"}}]),
                "validation": {"negation": True},
            }
        )
        self.assertEqual(decision.status, "rejected")

    def test_invalid_arguments_are_rejected(self):
        decision, _ = self.route(response([{"name": "get_weather", "arguments": {"city": 42}}]))
        self.assertEqual(decision.status, "rejected")
        self.assertIn("schema validation failed", decision.reason)

    def test_tool_outside_candidates_is_an_error(self):
        with self.assertRaisesRegex(RouterError, "outside the candidate set"):
            self.route(response([{"name": "shell", "arguments": {}}]))

    def test_disallowed_effect_is_rejected(self):
        decision, _ = self.route(
            response([{"name": "write_file", "arguments": {"path": "a", "content": "b"}}]),
            tools=(WRITE_FILE,),
        )
        self.assertEqual(decision.status, "rejected")
        self.assertIn("effect 'write'", decision.reason)

    def test_allowed_write_can_still_require_confirmation(self):
        tool = ToolSpec(**{**WRITE_FILE.__dict__, "requires_confirmation": True})
        settings = RouterSettings(allowed_effects=frozenset({"read", "write"}))
        decision, _ = self.route(
            response([{"name": "write_file", "arguments": {"path": "a", "content": "b"}}]),
            tools=(tool,),
            settings=settings,
        )
        self.assertEqual(decision.status, "confirm")

    def test_role_policy_is_enforced(self):
        restricted = ToolSpec(**{**WEATHER.__dict__, "allowed_roles": frozenset({"admin"})})
        decision, _ = self.route(
            response([{"name": "get_weather", "arguments": {"city": "Lagos"}}]),
            tools=(restricted,),
        )
        self.assertEqual(decision.status, "rejected")

    def test_candidate_set_is_bounded(self):
        tools = tuple(ToolSpec(f"tool_{index}", "Read.", {"type": "object"}) for index in range(6))
        with self.assertRaisesRegex(RouterError, "maximum is 5"):
            self.route(response([]), tools=tools)

    def test_multiple_calls_require_decomposition(self):
        decision, _ = self.route(
            response(
                [
                    {"name": "get_weather", "arguments": {"city": "Lagos"}},
                    {"name": "get_weather", "arguments": {"city": "Paris"}},
                ]
            )
        )
        self.assertEqual(decision.status, "confirm")
        self.assertIsNone(decision.tool)

    def test_gateway_executes_only_selected_decisions(self):
        gateway = ToolGateway({"get_weather": lambda city: {"city": city}})
        selected = ToolDecision("selected", "get_weather", {"city": "Lagos"}, 0.9, "ok")
        self.assertEqual(gateway.execute(selected), {"city": "Lagos"})
        for status in ("abstain", "confirm", "rejected"):
            with self.subTest(status=status), self.assertRaises(PermissionError):
                gateway.execute(ToolDecision(status, "get_weather", {}, 0.5, "no"))

    def test_backend_uses_complete_and_never_run(self):
        agent = mock.MagicMock()
        needle_module = mock.MagicMock()
        needle_module.Needle.return_value = agent
        agent.complete.return_value = response([])
        with mock.patch.dict("sys.modules", {"needle": needle_module}):
            result = NeedleBackend(custom_weights="/models/needle3.cact").select(
                "intent", [WEATHER.needle_schema()], 128
            )
        self.assertEqual(result["function_calls"], [])
        agent.complete.assert_called_once_with("intent", max_new_tokens=128)
        agent.run.assert_not_called()
        agent.close.assert_called_once()
        self.assertNotIn("stateless", needle_module.Needle.call_args.kwargs)
        self.assertEqual(needle_module.Needle.call_args.kwargs["weights"], "/models/needle3.cact")

    def test_stock_backend_omits_weights_and_uses_in_process_base(self):
        agent = mock.MagicMock()
        needle_module = mock.MagicMock()
        needle_module.Needle.return_value = agent
        agent.complete.return_value = response([])
        with mock.patch.dict("sys.modules", {"needle": needle_module}):
            NeedleBackend().select("intent", [WEATHER.needle_schema()], 128)
        self.assertNotIn("weights", needle_module.Needle.call_args.kwargs)
        self.assertEqual(needle_module.Needle.call_args.kwargs["generation"], 3)

    def test_invalid_threshold_configuration_fails(self):
        with mock.patch.dict(
            "os.environ",
            {"NEEDLE_CONFIRM_THRESHOLD": "0.8", "NEEDLE_CONFIDENCE_THRESHOLD": "0.7"},
        ):
            with self.assertRaises(RouterError):
                RouterSettings.from_env()


if __name__ == "__main__":
    unittest.main()

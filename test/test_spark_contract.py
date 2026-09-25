import unittest
from unittest import mock

from integration.spark_contract import ContractError, Settings, is_retryable, validate_request, verify_remote_models


def request(**overrides):
    payload = {"model": "spark-x2.5-1.7b", "messages": [{"role": "user", "content": "hello"}]}
    payload.update(overrides)
    return payload


class SparkRequestContractTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()

    def assert_error(self, code, payload, **kwargs):
        with self.assertRaises(ContractError) as raised:
            validate_request(payload, self.settings, **kwargs)
        self.assertEqual(raised.exception.payload["error"], code)
        return raised.exception

    def test_normal_request_below_limit(self):
        clean, estimate = validate_request(request(max_tokens=100), self.settings, estimated_tokens=1000)
        self.assertEqual((clean["max_tokens"], estimate), (100, 1000))

    def test_exact_safe_budget_boundary(self):
        clean, _ = validate_request(request(max_tokens=4096), self.settings, estimated_tokens=24576)
        self.assertEqual(clean["max_tokens"], 4096)

    def test_oversized_input(self):
        self.assertEqual(
            self.assert_error("spark_context_budget_exceeded", request(), estimated_tokens=24577).status, 413
        )

    def test_oversized_max_tokens_is_clamped(self):
        clean, _ = validate_request(request(max_tokens=99999), self.settings, estimated_tokens=1)
        self.assertEqual(clean["max_tokens"], 4096)

    def test_combined_budget_overflow(self):
        custom = Settings(max_input_tokens=30000)
        with self.assertRaises(ContractError) as raised:
            validate_request(request(max_tokens=4096), custom, estimated_tokens=24577)
        self.assertEqual(raised.exception.payload["error"], "spark_context_budget_exceeded")

    def test_unsupported_parameter(self):
        self.assertEqual(
            self.assert_error("spark_unsupported_parameters", request(seed=7)).payload["parameters"], ["seed"]
        )

    def test_model_alias_is_normalized(self):
        clean, _ = validate_request(request(model="spark-worker"), self.settings)
        self.assertEqual(clean["model"], "spark-x2.5-1.7b")

    def test_remote_model_mismatch_detection(self):
        with self.assertRaises(ContractError) as raised:
            verify_remote_models({"data": [{"id": "different-model"}]}, self.settings.model)
        self.assertEqual(raised.exception.payload["error"], "spark_remote_model_mismatch")

    def test_default_parameters(self):
        clean, _ = validate_request(request(), self.settings)
        self.assertEqual((clean["temperature"], clean["top_p"], clean["max_tokens"]), (0.2, 0.95, 4096))

    def test_retry_classification(self):
        self.assertTrue(is_retryable(503))
        self.assertTrue(is_retryable(connection_error=True))
        self.assertFalse(is_retryable(429))
        self.assertFalse(is_retryable(401))
        self.assertFalse(is_retryable(503, response_started=True))

    def test_streaming_is_preserved(self):
        clean, _ = validate_request(request(stream=True), self.settings)
        self.assertTrue(clean["stream"])

    def test_headroom_compressed_request_becomes_valid(self):
        self.assert_error("spark_context_budget_exceeded", request(), estimated_tokens=25000)
        clean, _ = validate_request(request(), self.settings, estimated_tokens=20000)
        self.assertEqual(clean["model"], self.settings.model)

    def test_post_compression_request_still_too_large(self):
        error = self.assert_error("spark_context_budget_exceeded", request(), estimated_tokens=25000)
        self.assertEqual(error.payload["estimated_input_tokens"], 25000)

    def test_max_completion_tokens_is_normalized(self):
        clean, _ = validate_request(request(max_completion_tokens=50), self.settings)
        self.assertNotIn("max_completion_tokens", clean)
        self.assertEqual(clean["max_tokens"], 50)

    def test_physical_context_ceiling_cannot_be_raised(self):
        with mock.patch.dict("os.environ", {"SPARK_CONTEXT_LIMIT": "32769"}):
            with self.assertRaisesRegex(ValueError, "physical ceiling 32768"):
                Settings.from_env()

    def test_boolean_sampling_values_are_rejected(self):
        self.assert_error("spark_invalid_request", request(temperature=True))
        self.assert_error("spark_invalid_request", request(top_p=False))

    def test_invalid_environment_defaults_are_rejected(self):
        cases = (
            ({"SPARK_CONNECT_TIMEOUT": "0"}, "timeouts must be positive"),
            ({"SPARK_READ_TIMEOUT": "-1"}, "timeouts must be positive"),
            ({"SPARK_TEMPERATURE": "3"}, "SPARK_TEMPERATURE"),
            ({"SPARK_TOP_P": "0"}, "SPARK_TOP_P"),
            ({"SPARK_MAX_RETRIES": "4"}, "SPARK_MAX_RETRIES"),
        )
        for environment, message in cases:
            with self.subTest(environment=environment):
                with mock.patch.dict("os.environ", environment):
                    with self.assertRaisesRegex(ValueError, message):
                        Settings.from_env()


if __name__ == "__main__":
    unittest.main()

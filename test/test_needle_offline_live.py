"""Opt-in real Needle3 offline test; requires build-prepared pinned artifacts."""

import os
import unittest
from unittest import mock

from integration.needle_runtime import preflight
from integration.tool_router import NeedleBackend, ToolRouter, ToolSpec


@unittest.skipUnless(os.getenv("NEEDLE_OFFLINE_TEST") == "1", "set NEEDLE_OFFLINE_TEST=1 with baked artifacts")
class NeedleOfflineLiveTests(unittest.TestCase):
    def test_real_selection_uses_no_huggingface_download(self):
        tool = ToolSpec(
            "read_file",
            "Read one repository file.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        )
        with mock.patch(
            "huggingface_hub.hf_hub_download",
            side_effect=AssertionError("offline inference attempted a Hugging Face download"),
        ):
            status = preflight()
            decision = ToolRouter(NeedleBackend()).route("Read integration/spark_contract.py", [tool])
        self.assertTrue(status["runtime_verified"])
        self.assertIn(decision.status, {"selected", "confirm"})
        self.assertEqual(decision.tool, "read_file")
        self.assertEqual(decision.arguments, {"path": "integration/spark_contract.py"})
        self.assertIsNotNone(decision.confidence)


if __name__ == "__main__":
    unittest.main()

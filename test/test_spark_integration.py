import json
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "integration" / "render-plandex-models.py"


class SparkIntegrationConfigTests(unittest.TestCase):
    def render(self, **env):
        merged = os.environ.copy()
        merged.update(env)
        return subprocess.run(
            [str(RENDERER)], env=merged, text=True, capture_output=True, check=False
        )

    def test_plandex_routes_only_through_loopback_headroom(self):
        result = self.render(SPARK_MODEL="spark-x2.5-1.7b")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        provider = config["providers"][0]
        self.assertEqual(provider["baseUrl"], "http://127.0.0.1:8787/v1")
        self.assertEqual(provider["apiKeyEnvVar"], "SPARK_API_KEY")
        self.assertEqual(
            config["models"][0]["providers"][0]["modelName"], "spark-x2.5-1.7b"
        )
        self.assertNotIn("leon4gr45-llama.hf.space", result.stdout)
        self.assertNotIn("llama-swap", result.stdout)

    def test_model_id_is_required_instead_of_fabricated(self):
        result = self.render(SPARK_MODEL="")
        self.assertEqual(result.returncode, 2)
        self.assertIn("SPARK_MODEL must be set", result.stderr)

    def test_nonbaseline_headroom_url_is_rejected(self):
        result = self.render(
            SPARK_MODEL="spark-x2.5-1.7b",
            HEADROOM_BASE_URL="https://leon4gr45-llama.hf.space/v1",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be http://127.0.0.1:8787/v1", result.stderr)


if __name__ == "__main__":
    unittest.main()

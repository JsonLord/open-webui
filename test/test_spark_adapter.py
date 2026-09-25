import json
import os
import socket
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "integration" / "spark-contract-server.py"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeSparkHandler(BaseHTTPRequestHandler):
    received = []

    def log_message(self, *_args):
        return

    def do_GET(self):
        body = json.dumps({"object": "list", "data": [{"id": "spark-x2.5-1.7b"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        payload = json.loads(body)
        self.received.append(payload)
        if payload["stream"]:
            response = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
            content_type = "text/event-stream"
        else:
            response = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class SparkAdapterIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FakeSparkHandler.received = []
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeSparkHandler)
        cls.upstream_thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.upstream_thread.start()
        cls.contract_port = free_port()
        env = os.environ.copy()
        env.update(
            SPARK_API_KEY="test-only",
            SPARK_BASE_URL=f"http://127.0.0.1:{cls.upstream.server_port}/v1",
            SPARK_CONTRACT_HOST="127.0.0.1",
            SPARK_CONTRACT_PORT=str(cls.contract_port),
        )
        cls.contract = subprocess.Popen(
            [str(SERVER)], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cls.contract_port}/readyz", timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            cls.contract.terminate()
            raise RuntimeError("Spark contract test server did not become ready")

    @classmethod
    def tearDownClass(cls):
        cls.contract.terminate()
        cls.contract.wait(timeout=5)
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def post(self, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.contract_port}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return urllib.request.urlopen(request, timeout=5)

    def test_non_streaming_request_is_sanitized_and_forwarded(self):
        with self.post({"model": "spark-worker", "messages": [{"role": "user", "content": "hello"}]}) as response:
            document = json.load(response)
            self.assertGreater(int(response.headers["X-Spark-Estimated-Input-Tokens"]), 0)
        self.assertEqual(document["choices"][0]["message"]["content"], "ok")
        forwarded = FakeSparkHandler.received[-1]
        self.assertEqual(forwarded["model"], "spark-x2.5-1.7b")
        self.assertEqual(forwarded["max_tokens"], 4096)
        self.assertEqual(set(forwarded), {"model", "messages", "stream", "max_tokens", "temperature", "top_p"})

    def test_streaming_response_is_preserved(self):
        with self.post(
            {"model": "spark-x2.5-1.7b", "messages": [{"role": "user", "content": "stream"}], "stream": True}
        ) as response:
            body = response.read().decode()
            self.assertEqual(response.headers.get_content_type(), "text/event-stream")
        self.assertIn("data:", body)
        self.assertIn("[DONE]", body)

    def test_unsupported_parameter_is_rejected_before_upstream(self):
        before = len(FakeSparkHandler.received)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.post({"model": "spark-worker", "messages": [{"role": "user", "content": "hello"}], "seed": 1})
        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(len(FakeSparkHandler.received), before)

    def test_models_endpoint_is_checked_and_forwarded(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.contract_port}/v1/models", timeout=5) as response:
            document = json.load(response)
        self.assertEqual(document["data"][0]["id"], "spark-x2.5-1.7b")


if __name__ == "__main__":
    unittest.main()

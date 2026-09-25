#!/usr/bin/env python3
"""Small loopback OpenAI adapter enforcing the final Spark request contract."""

from __future__ import annotations

import http.client
import json
import os
import ssl
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integration.spark_contract import ContractError, Settings, is_retryable, validate_request, verify_remote_models

SETTINGS = Settings.from_env()
BASE = urllib.parse.urlsplit(os.environ.get("SPARK_BASE_URL", "https://leon4gr45-llama.hf.space/v1").rstrip("/"))
API_KEY = os.environ.get("SPARK_API_KEY") or os.environ.get("SPARK_API_TOKEN", "")
if BASE.scheme not in {"http", "https"} or not BASE.hostname or BASE.query or BASE.fragment:
    raise SystemExit("error: SPARK_BASE_URL must be an http(s) URL without query or fragment")
METRICS = {
    "requests": 0,
    "failures": 0,
    "retries": 0,
    "estimated_input_tokens": 0,
    "final_request_bytes": 0,
    "upstream_latency_ms": 0,
}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("spark-contract:", fmt % args, file=sys.stderr)

    def json_response(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/livez", "/readyz"):
            return self.json_response(200, {"status": "ok", "model": SETTINGS.model})
        if self.path == "/metrics":
            with LOCK:
                return self.json_response(200, dict(METRICS))
        if self.path == "/v1/models":
            return self.forward("GET", "/models", None, inspect_models=True)
        return self.json_response(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self.json_response(404, {"error": "not_found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16 * 1024 * 1024:
                raise ContractError(400, "spark_invalid_request", "invalid request body size")
            payload = json.loads(self.rfile.read(length))
            clean, estimate = validate_request(payload, SETTINGS)
            encoded = json.dumps(clean, separators=(",", ":")).encode()
            with LOCK:
                METRICS["estimated_input_tokens"] += estimate
                METRICS["final_request_bytes"] += len(encoded)
            self.forward("POST", "/chat/completions", encoded, estimated_tokens=estimate)
        except ContractError as exc:
            with LOCK:
                METRICS["failures"] += 1
            self.json_response(exc.status, exc.payload)
        except (json.JSONDecodeError, ValueError) as exc:
            with LOCK:
                METRICS["failures"] += 1
            self.json_response(400, {"error": "spark_invalid_request", "message": str(exc)})

    def forward(self, method, suffix, body, *, inspect_models=False, estimated_tokens=None):
        path = f"{BASE.path.rstrip('/')}{suffix}"
        headers = {"Accept": self.headers.get("Accept", "application/json")}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        if API_KEY:
            headers["Authorization"] = f"Bearer {API_KEY}"
        attempts = SETTINGS.max_retries + 1
        for attempt in range(attempts):
            conn = None
            request_sent = False
            started = time.monotonic()
            try:
                cls = http.client.HTTPSConnection if BASE.scheme == "https" else http.client.HTTPConnection
                kwargs = {"timeout": SETTINGS.connect_timeout}
                if BASE.scheme == "https":
                    kwargs["context"] = ssl.create_default_context()
                conn = cls(BASE.hostname, BASE.port, **kwargs)
                conn.connect()
                conn.sock.settimeout(SETTINGS.read_timeout)
                conn.request(method, path, body=body, headers=headers)
                request_sent = True
                response = conn.getresponse()
                if is_retryable(response.status) and attempt + 1 < attempts:
                    response.read()
                    with LOCK:
                        METRICS["retries"] += 1
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raw = response.read() if inspect_models else None
                if inspect_models and response.status < 400:
                    try:
                        document = json.loads(raw)
                        verify_remote_models(document, SETTINGS.model)
                    except json.JSONDecodeError as exc:
                        raise ContractError(
                            502,
                            "spark_invalid_upstream_response",
                            "remote /models did not return valid JSON",
                        ) from exc
                self.send_response(response.status)
                excluded = {"connection", "transfer-encoding", "content-length", "content-encoding"}
                for key, value in response.getheaders():
                    if key.lower() not in excluded:
                        self.send_header(key, value)
                if raw is not None:
                    self.send_header("Content-Length", str(len(raw)))
                else:
                    self.send_header("Connection", "close")
                if estimated_tokens is not None:
                    self.send_header("X-Spark-Estimated-Input-Tokens", str(estimated_tokens))
                self.end_headers()
                if raw is not None:
                    self.wfile.write(raw)
                else:
                    while chunk := response.read(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                with LOCK:
                    METRICS["requests"] += 1
                    METRICS["upstream_latency_ms"] += round((time.monotonic() - started) * 1000)
                return
            except ContractError as exc:
                return self.json_response(exc.status, exc.payload)
            except (OSError, http.client.HTTPException) as exc:
                # Once a generation POST was sent, a missing response is
                # ambiguous; never risk duplicating that long-running request.
                safe_connection_retry = not request_sent or method == "GET"
                if attempt + 1 < attempts and safe_connection_retry and is_retryable(connection_error=True):
                    with LOCK:
                        METRICS["retries"] += 1
                    time.sleep(0.5 * (attempt + 1))
                    continue
                with LOCK:
                    METRICS["failures"] += 1
                return self.json_response(502, {"error": "spark_upstream_unavailable", "message": str(exc)})
            finally:
                if conn:
                    conn.close()


if __name__ == "__main__":
    if not API_KEY:
        raise SystemExit("error: SPARK_API_KEY or SPARK_API_TOKEN is required")
    host = os.getenv("SPARK_CONTRACT_HOST", "127.0.0.1")
    port = int(os.getenv("SPARK_CONTRACT_PORT", "8790"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()

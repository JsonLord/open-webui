#!/usr/bin/env python3
"""Make real OpenAI-compatible model and chat calls to Spark or Headroom."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def request(url: str, api_key: str, *, payload: dict | None = None, timeout: float = 60):
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    started = time.monotonic()
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, time.monotonic() - started, response.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{url} returned HTTP {exc.code}: {detail[:500]}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--via", choices=("spark", "headroom"), default="headroom")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    token = os.environ.get("SPARK_API_KEY") or os.environ.get("SPARK_API_TOKEN", "")
    model = os.environ.get("SPARK_MODEL", "spark-x2.5-1.7b").strip()
    base = (
        os.environ.get("SPARK_BASE_URL", "https://leon4gr45-llama.hf.space/v1")
        if args.via == "spark"
        else os.environ.get("HEADROOM_BASE_URL", "http://127.0.0.1:8787/v1")
    ).rstrip("/")
    if not token:
        print("error: set SPARK_API_KEY or SPARK_API_TOKEN", file=sys.stderr)
        return 2

    try:
        status, elapsed, body = request(f"{base}/models", token, timeout=args.timeout)
        models = json.loads(body)
        ids = [item.get("id") for item in models.get("data", [])]
        print(json.dumps({"endpoint": f"{base}/models", "status": status, "seconds": round(elapsed, 3), "models": ids}))
        if model not in ids:
            raise RuntimeError(f"configured model {model!r} not advertised by /models: {ids!r}")

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: spark-ok"}],
            "max_tokens": min(16, int(os.environ.get("SPARK_MAX_OUTPUT_TOKENS", "4096"))),
            "temperature": float(os.environ.get("SPARK_TEMPERATURE", "0.2")),
            "top_p": float(os.environ.get("SPARK_TOP_P", "0.95")),
            "stream": args.stream,
        }
        status, elapsed, body = request(
            f"{base}/chat/completions", token, payload=payload, timeout=args.timeout
        )
        if args.stream:
            valid = "data:" in body and "[DONE]" in body
        else:
            parsed = json.loads(body)
            valid = bool(parsed.get("choices"))
        if not valid:
            raise RuntimeError(f"unexpected completion response: {body[:500]}")
        print(json.dumps({"endpoint": f"{base}/chat/completions", "status": status, "seconds": round(elapsed, 3), "model": model, "stream": args.stream, "valid": True}))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

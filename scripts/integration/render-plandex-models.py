#!/usr/bin/env python3
"""Render the Plandex custom provider/model pack from deployment variables."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "integration" / "plandex-models.template.json"


def render() -> dict:
    model = os.environ.get("SPARK_MODEL", "").strip()
    if not model:
        raise ValueError("SPARK_MODEL must be set to the model ID returned by Spark")
    if model != "spark-x2.5-1.7b":
        raise ValueError("SPARK_MODEL must match the authoritative model ID spark-x2.5-1.7b")
    headroom = os.environ.get(
        "HEADROOM_BASE_URL", "http://127.0.0.1:8787/v1"
    ).rstrip("/")
    if headroom != "http://127.0.0.1:8787/v1":
        raise ValueError(
            "HEADROOM_BASE_URL must be http://127.0.0.1:8787/v1 for baseline Plandex routing"
        )

    document = TEMPLATE.read_text(encoding="utf-8")
    document = document.replace("${HEADROOM_BASE_URL}", headroom)
    document = document.replace("${SPARK_MODEL}", model)
    return json.loads(document)


def main() -> int:
    try:
        document = render()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    json.dump(document, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

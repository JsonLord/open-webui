#!/usr/bin/env python3
"""Verify baked Needle3 artifacts and initialize the stock model offline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integration.needle_runtime import preflight

if __name__ == "__main__":
    try:
        print(json.dumps({"needle": preflight()}))
    except Exception as exc:
        print(json.dumps({"needle": {"installed": False, "runtime_verified": False, "error": str(exc)}}))
        raise SystemExit(1)

#!/usr/bin/env python3
"""Live local Needle3 smoke test; selects a tool but never executes it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integration.tool_router import NeedleBackend, ToolRouter, ToolSpec


def main() -> int:
    candidates = [
        ToolSpec(
            name="get_weather",
            description="Read the current weather for a city.",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            },
        )
    ]
    try:
        decision = ToolRouter(NeedleBackend()).route("What is the weather in Lagos?", candidates)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": decision.status,
                "tool": decision.tool,
                "arguments": decision.arguments,
                "confidence": decision.confidence,
                "reason": decision.reason,
            }
        )
    )
    return 0 if decision.status in {"selected", "confirm"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

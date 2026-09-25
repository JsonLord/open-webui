#!/usr/bin/env python3
"""Fail closed before starting Plandex when its baked tokenizer is invalid."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))

from open_webui.control_plane.plandex_tokenizer import preflight

try:
    artifact = preflight()
except RuntimeError as exc:
    print(f'plandex_tokenizer_unavailable: {exc}', file=sys.stderr)
    raise SystemExit(1)
print(f'Plandex tokenizer ready: {artifact.name} ({artifact.stat().st_size} bytes)')

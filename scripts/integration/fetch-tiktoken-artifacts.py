#!/usr/bin/env python3
"""Build-time-only acquisition of Plandex's pinned o200k cache artifact."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from plandex_tokenizer_runtime import tokenizer

DEFAULT_CACHE_DIR = tokenizer.DEFAULT_CACHE_DIR
MIRROR_URL = tokenizer.MIRROR_URL
fetch_and_install = tokenizer.fetch_and_install
preflight = tokenizer.preflight


def main() -> int:
    cache_dir = Path(os.getenv('TIKTOKEN_CACHE_DIR', DEFAULT_CACHE_DIR))
    try:
        try:
            path = preflight(cache_dir)
            print(f'Reusing verified tokenizer artifact: {path}')
        except RuntimeError:
            path = fetch_and_install(cache_dir, os.getenv('TIKTOKEN_O200K_FETCH_URL') or MIRROR_URL)
            print(f'Installed verified tokenizer artifact: {path}')
        print(f'size={path.stat().st_size}')
        return 0
    except Exception as exc:
        print(f'Tokenizer artifact preparation failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env bash
# Targeted Phase 3 runtime preparation. Intentionally does not traverse Python
# requirements or Node workspaces (especially unrelated plandex/docs).
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$repo_root"

python scripts/integration/fetch-tiktoken-artifacts.py
python scripts/integration/plandex-tokenizer-preflight.py

printf '%s\n' 'Phase 3 tokenizer runtime is prepared; no repository-wide dependency bootstrap was run.'

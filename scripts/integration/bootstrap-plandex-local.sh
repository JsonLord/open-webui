#!/usr/bin/env bash
# Run after start-plandex-server.sh has been launched by the process supervisor.
# This is the readiness gate: it never starts authentication from a task worker.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
: "${PLANDEX_INTEGRATION_PYTHON:=python3}"

"$repo_root/scripts/integration/wait-plandex.sh"
"$PLANDEX_INTEGRATION_PYTHON" "$repo_root/scripts/integration/plandex-tokenizer-preflight.py"
"$PLANDEX_INTEGRATION_PYTHON" "$repo_root/scripts/integration/plandex-auth-preflight.py"

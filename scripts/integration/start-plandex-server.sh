#!/usr/bin/env bash
set -euo pipefail

: "${PLANDEX_DATABASE_URL:?PLANDEX_DATABASE_URL must target the dedicated Plandex database}"
: "${PLANDEX_BASE_DIR:=/var/lib/plandex-server}"
: "${PLANDEX_SERVER_HOST:=127.0.0.1}"
: "${PLANDEX_SERVER_PORT:=8099}"
: "${PLANDEX_SERVER_BINARY:=plandex-server}"
: "${PLANDEX_SERVER_WORK_DIR:=/opt/plandex-server}"
: "${TIKTOKEN_CACHE_DIR:=/opt/integration/tiktoken-cache}"
: "${PLANDEX_INTEGRATION_PYTHON:=python3}"

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
TIKTOKEN_CACHE_DIR="$TIKTOKEN_CACHE_DIR" "$PLANDEX_INTEGRATION_PYTHON" "$repo_root/scripts/integration/plandex-tokenizer-preflight.py"
mkdir -p "$PLANDEX_BASE_DIR"
chmod 0700 "$PLANDEX_BASE_DIR"

export GOENV=development LOCAL_MODE=1 PLANDEX_BASE_DIR TIKTOKEN_CACHE_DIR
export HOST="$PLANDEX_SERVER_HOST"
export PORT="$PLANDEX_SERVER_PORT"
export DATABASE_URL="$PLANDEX_DATABASE_URL"
cd "$PLANDEX_SERVER_WORK_DIR"
exec "$PLANDEX_SERVER_BINARY"

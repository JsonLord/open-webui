#!/usr/bin/env bash
set -euo pipefail
: "${PLANDEX_API_HOST:=http://127.0.0.1:8099}"
: "${PLANDEX_READY_TIMEOUT:=60}"

deadline=$((SECONDS + PLANDEX_READY_TIMEOUT))
until curl --silent --fail "$PLANDEX_API_HOST/health" >/dev/null; do
  if (( SECONDS >= deadline )); then
    echo "Plandex readiness timed out" >&2
    exit 1
  fi
  sleep 1
done

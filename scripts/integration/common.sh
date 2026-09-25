#!/usr/bin/env bash

set -euo pipefail

spark_load_env() {
  : "${SPARK_BASE_URL:=https://leon4gr45-llama.hf.space/v1}"
  : "${HEADROOM_BASE_URL:=http://127.0.0.1:8787/v1}"
  : "${HEADROOM_HOST:=127.0.0.1}"
  : "${HEADROOM_PORT:=8787}"
  : "${SPARK_MODEL:=spark-x2.5-1.7b}"
  : "${SPARK_CONTEXT_LIMIT:=32768}"
  : "${SPARK_MAX_INPUT_TOKENS:=24576}"
  : "${SPARK_MAX_OUTPUT_TOKENS:=4096}"
  : "${SPARK_CONTEXT_RESERVE:=4096}"
  : "${SPARK_TEMPERATURE:=0.2}"
  : "${SPARK_TOP_P:=0.95}"
  : "${SPARK_CONNECT_TIMEOUT:=15}"
  : "${SPARK_READ_TIMEOUT:=300}"
  : "${SPARK_MAX_RETRIES:=1}"
  : "${SPARK_CONTRACT_HOST:=127.0.0.1}"
  : "${SPARK_CONTRACT_PORT:=8790}"

  # SPARK_API_TOKEN is a temporary deployment-secret name. Headroom and
  # Plandex use the canonical SPARK_API_KEY name internally.
  if [[ -z "${SPARK_API_KEY:-}" && -n "${SPARK_API_TOKEN:-}" ]]; then
    SPARK_API_KEY=$SPARK_API_TOKEN
  fi
  export SPARK_BASE_URL SPARK_MODEL HEADROOM_BASE_URL HEADROOM_HOST HEADROOM_PORT
  export SPARK_CONTEXT_LIMIT SPARK_MAX_INPUT_TOKENS SPARK_MAX_OUTPUT_TOKENS
  export SPARK_CONTEXT_RESERVE SPARK_TEMPERATURE SPARK_TOP_P
  export SPARK_CONNECT_TIMEOUT SPARK_READ_TIMEOUT SPARK_MAX_RETRIES
  export SPARK_CONTRACT_HOST SPARK_CONTRACT_PORT
  export SPARK_API_KEY="${SPARK_API_KEY:-}"
}

spark_require() {
  local name
  for name in "$@"; do
    if [[ -z "${!name:-}" ]]; then
      printf 'error: required environment variable %s is unset or empty\n' "$name" >&2
      return 2
    fi
  done
}

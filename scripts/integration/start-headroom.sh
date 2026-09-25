#!/usr/bin/env bash

set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
spark_load_env
spark_require SPARK_API_KEY SPARK_CONTRACT_HOST SPARK_CONTRACT_PORT

command -v headroom >/dev/null 2>&1 || {
  echo 'error: headroom is not installed; install integration/requirements.txt' >&2
  exit 127
}

# Final validation must happen after compression, so Headroom forwards to the
# loopback request-contract adapter rather than directly to remote Spark.
upstream="http://${SPARK_CONTRACT_HOST}:${SPARK_CONTRACT_PORT}"

# Headroom falls back to OPENAI_API_KEY when the caller does not supply auth.
# Plandex also sends SPARK_API_KEY through the provider configuration, so both
# direct smoke calls and Plandex calls authenticate without exposing secrets.
export OPENAI_API_KEY=$SPARK_API_KEY
export HEADROOM_BEACON=off
export HEADROOM_UPDATE_CHECK=off
export HEADROOM_TELEMETRY=on

exec headroom proxy \
  --host "$HEADROOM_HOST" \
  --port "$HEADROOM_PORT" \
  --openai-api-url "$upstream" \
  --provider-name Spark

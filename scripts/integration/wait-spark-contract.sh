#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
spark_load_env
timeout=${SPARK_CONTRACT_READY_TIMEOUT:-30}
deadline=$((SECONDS + timeout))
url="http://${SPARK_CONTRACT_HOST}:${SPARK_CONTRACT_PORT}/readyz"
until curl --fail --silent --show-error "$url" >/dev/null; do
  if (( SECONDS >= deadline )); then
    printf 'error: Spark contract did not become ready at %s within %ss\n' "$url" "$timeout" >&2
    exit 1
  fi
  sleep 1
done
printf 'Spark contract is ready at %s\n' "$url"

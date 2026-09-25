#!/usr/bin/env bash

set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
spark_load_env

timeout=${HEADROOM_READY_TIMEOUT:-60}
deadline=$((SECONDS + timeout))
url="http://${HEADROOM_HOST}:${HEADROOM_PORT}/livez"
until curl --fail --silent --show-error "$url" >/dev/null; do
  if (( SECONDS >= deadline )); then
    printf 'error: Headroom did not become live at %s within %ss\n' "$url" "$timeout" >&2
    exit 1
  fi
  sleep 1
done
printf 'Headroom is live at %s\n' "$url"

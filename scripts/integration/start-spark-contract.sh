#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
spark_load_env
spark_require SPARK_BASE_URL SPARK_MODEL SPARK_API_KEY
exec python3 "$SCRIPT_DIR/spark-contract-server.py"

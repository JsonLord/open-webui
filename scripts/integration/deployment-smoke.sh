#!/usr/bin/env bash
set -euo pipefail

# Machine-readable deployment readiness. Critical failures return non-zero;
# optional capability failures are explicit degradation and preserve readiness.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
REPORT_PATH=${1:-$ROOT_DIR/deployment-readiness-report.json}
APP_PERSIST_ROOT=${APP_PERSIST_ROOT:-/data/agent-platform}
REQUIRE_PERSISTENT_STORAGE=${REQUIRE_PERSISTENT_STORAGE:-0}
PLANDEX_TIKTOKEN_CACHE_DIR=${PLANDEX_TIKTOKEN_CACHE_DIR:-/opt/integration/tiktoken-cache}
GRAPHIFY_EXECUTABLE=${GRAPHIFY_EXECUTABLE:-/opt/integration/graphify-venv/bin/graphify}

status_from() {
  local override=$1; shift
  if [[ -n "$override" ]]; then printf '%s' "$override"; return; fi
  if "$@" >/dev/null 2>&1; then printf ready; else printf failed; fi
}
port_status() { ss -H -ltn | awk -v endpoint="$1" '$4 == endpoint {ok=1} END {exit !ok}'; }

storage_probe() { mkdir -p "$APP_PERSIST_ROOT" && touch "$APP_PERSIST_ROOT/.smoke-write" && rm -f "$APP_PERSIST_ROOT/.smoke-write"; }
if [[ "$REQUIRE_PERSISTENT_STORAGE" == 1 ]]; then
  storage=$(status_from "${SMOKE_STORAGE_STATUS:-}" storage_probe)
else
  storage=ready
fi
postgres=$(status_from "${SMOKE_POSTGRES_STATUS:-}" pg_isready -h 127.0.0.1 -p 5432)
plandex=$(status_from "${SMOKE_PLANDEX_STATUS:-}" curl -fsS http://127.0.0.1:8099/health)
tokenizer=$(status_from "${SMOKE_TOKENIZER_STATUS:-}" env TIKTOKEN_CACHE_DIR="$PLANDEX_TIKTOKEN_CACHE_DIR" python3 "$SCRIPT_DIR/plandex-tokenizer-preflight.py")
plandex_auth=$(status_from "${SMOKE_PLANDEX_AUTH_STATUS:-}" env TIKTOKEN_CACHE_DIR="$PLANDEX_TIKTOKEN_CACHE_DIR" python3 "$SCRIPT_DIR/plandex-auth-preflight.py")
open_webui=$(status_from "${SMOKE_OPEN_WEBUI_STATUS:-}" curl -fsS http://127.0.0.1:7860/health)

public_guard_probe() {
  port_status 0.0.0.0:7860 &&
    ! ss -H -ltn | awk '$4 ~ /^0\.0\.0\.0:/ && $4 != "0.0.0.0:7860" {bad=1} END {exit !bad}'
}
public_guard=$(status_from "${SMOKE_PUBLIC_GUARD_STATUS:-}" public_guard_probe)
# Required services must also obey their loopback contracts.
[[ $(status_from "${SMOKE_POSTGRES_PORT_STATUS:-}" port_status 127.0.0.1:5432) == ready ]] || postgres=failed
[[ $(status_from "${SMOKE_PLANDEX_PORT_STATUS:-}" port_status 127.0.0.1:8099) == ready ]] || plandex=failed

spark_configured=false
[[ -n "${SPARK_API_KEY:-}" && -n "${SPARK_BASE_URL:-}" ]] && spark_configured=true
if [[ -n "${SMOKE_SPARK_STATUS:-}" ]]; then
  spark=$SMOKE_SPARK_STATUS
elif [[ "$spark_configured" == false ]]; then
  spark=not_configured
elif port_status 127.0.0.1:8787 && port_status 127.0.0.1:8790 && curl -fsS http://127.0.0.1:8790/readyz >/dev/null 2>&1; then
  spark=ready
else
  spark=unavailable
fi
headroom=$([[ "$spark" == ready ]] && echo ready || { [[ "$spark" == not_configured ]] && echo not_configured || echo unavailable; })

graphify=${SMOKE_GRAPHIFY_STATUS:-}
if [[ -z "$graphify" ]]; then
  if [[ ! -x "$GRAPHIFY_EXECUTABLE" ]]; then graphify=unavailable
  elif "$GRAPHIFY_EXECUTABLE" --version 2>/dev/null | grep -Fxq 'graphify 0.9.67'; then graphify=ready
  else graphify=failed; fi
fi
needle=${SMOKE_NEEDLE_STATUS:-}
if [[ -z "$needle" ]]; then
  if HF_HUB_OFFLINE=1 python3 "$SCRIPT_DIR/needle-preflight.py" >/dev/null 2>&1; then needle=ready; else needle=failed; fi
fi
jit=$([[ -n "${JIT_BASE_URL:-}" ]] && echo unavailable || echo not_configured)
github_authenticated=$([[ -n "${GITHUB_PAT:-}" ]] && echo ready || echo not_configured)

deployment_ready=true
for value in "$storage" "$postgres" "$plandex" "$tokenizer" "$plandex_auth" "$open_webui" "$public_guard"; do
  [[ "$value" == ready ]] || deployment_ready=false
done

mkdir -p "${REPORT_PATH%/*}"
cat >"$REPORT_PATH" <<EOF_JSON
{
  "deployment_ready": $deployment_ready,
  "critical": {
    "storage": "$storage",
    "postgres": "$postgres",
    "plandex": "$plandex",
    "tokenizer": "$tokenizer",
    "plandex_auth": "$plandex_auth",
    "open_webui": "$open_webui",
    "public_listener_guard": "$public_guard"
  },
  "degraded": {
    "graphify": "$graphify",
    "needle": "$needle",
    "spark": "$spark",
    "headroom": "$headroom",
    "jit": "$jit",
    "github_authenticated": "$github_authenticated"
  }
}
EOF_JSON
cat "$REPORT_PATH"
[[ "$deployment_ready" == true ]]

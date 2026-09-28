#!/usr/bin/env bash
set -euo pipefail

# Lightweight PID-1 supervisor for the single-container HF deployment.
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck source=common.sh
source "$repo_root/scripts/integration/common.sh"
spark_load_env

# HF's public port is a deployment contract, not an inherited upstream default.
export HOST=0.0.0.0 PORT=7860
export DATA_DIR="$OPEN_WEBUI_DATA_DIR"
export PLANDEX_TIKTOKEN_CACHE_DIR="${PLANDEX_TIKTOKEN_CACHE_DIR:-/opt/integration/tiktoken-cache}"
export GRAPHIFY_EXECUTABLE="${GRAPHIFY_EXECUTABLE:-/opt/integration/graphify-venv/bin/graphify}"
export PATH="/opt/integration/bin:${PATH}"

readonly PGPORT=5432
readonly PG_BINDIR="$(pg_config --bindir)"
declare -a CHILD_PIDS=() CHILD_NAMES=()
SHUTTING_DOWN=0
GRAPHIFY_STATE=not_configured
NEEDLE_STATE=not_configured
SPARK_STATE=not_configured
HEADROOM_STATE=not_configured

log() { printf '[Supervisor] %s\n' "$*"; }

shutdown() {
  local status="${1:-0}" deadline pid
  (( SHUTTING_DOWN )) && return
  SHUTTING_DOWN=1
  trap - TERM INT EXIT
  log "Stopping managed services"
  for pid in "${CHILD_PIDS[@]:-}"; do
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  done
  deadline=$((SECONDS + 10))
  while (( SECONDS < deadline )); do
    local alive=0
    for pid in "${CHILD_PIDS[@]:-}"; do kill -0 "$pid" 2>/dev/null && alive=1; done
    (( alive )) || break
    sleep 1
  done
  for pid in "${CHILD_PIDS[@]:-}"; do
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  if [[ -f "$PGDATA/PG_VERSION" ]]; then
    runuser -u postgres -- "$PG_BINDIR/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap 'shutdown 143' TERM
trap 'shutdown 130' INT
trap 'shutdown $?' EXIT

start_child() {
  local name=$1; shift
  setsid "$@" &
  CHILD_PIDS+=("$!")
  CHILD_NAMES+=("$name")
  log "$name started (pid $!)"
}

stop_child() {
  local pid=$1
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
}

write_readiness() {
  local destination="$APP_PERSIST_ROOT/runtime-state/deployment-readiness.json"
  mkdir -p "${destination%/*}"
  cat >"$destination.tmp" <<EOF
{
  "deployment_ready": true,
  "critical": {
    "storage": "ready",
    "postgres": "ready",
    "plandex": "ready",
    "tokenizer": "ready",
    "plandex_auth": "ready",
    "open_webui": "ready"
  },
  "degraded": {
    "graphify": "$GRAPHIFY_STATE",
    "needle": "$NEEDLE_STATE",
    "spark": "$SPARK_STATE",
    "headroom": "$HEADROOM_STATE",
    "jit": "${JIT_BASE_URL:+unavailable}",
    "github_authenticated": "${GITHUB_PAT:+unavailable}"
  }
}
EOF
  # Empty optional configuration expansions mean not_configured.
  sed -i 's/"jit": ""/"jit": "not_configured"/; s/"github_authenticated": ""/"github_authenticated": "not_configured"/' "$destination.tmp"
  mv "$destination.tmp" "$destination"
  log "Readiness state written to $destination"
}

require_persistence() {
  local probe="$APP_PERSIST_ROOT/.write-test-$$"
  mkdir -p "$APP_PERSIST_ROOT"
  if ! (umask 077 && : > "$probe") 2>/dev/null; then
    if [[ "${REQUIRE_PERSISTENT_STORAGE:-1}" == 1 ]]; then
      log "ERROR: required persistent root is not writable: $APP_PERSIST_ROOT"
      return 1
    fi
    log "WARNING: persistent root is not writable"
    return 0
  fi
  rm -f "$probe"
}

require_persistence
mkdir -p "$PGDATA" "$PLANDEX_BASE_DIR" "$PLANDEX_CLI_HOME" "$DATA_DIR" \
  "$CONTROL_PLANE_DATA_DIR" "$REPOSITORY_CACHE_DIR" "$GRAPHIFY_DATA_DIR"

# Prove every mutable component path is rooted in the one mounted volume.
export PGDATA PLANDEX_BASE_DIR PLANDEX_CLI_HOME CONTROL_PLANE_DATA_DIR REPOSITORY_CACHE_DIR GRAPHIFY_DATA_DIR
python3 - <<'PY'
import os
from pathlib import Path
root = Path(os.environ['APP_PERSIST_ROOT']).resolve()
for name in ('PGDATA', 'PLANDEX_BASE_DIR', 'PLANDEX_CLI_HOME', 'DATA_DIR',
             'CONTROL_PLANE_DATA_DIR', 'REPOSITORY_CACHE_DIR', 'GRAPHIFY_DATA_DIR'):
    path = Path(os.environ[name]).resolve()
    assert path == root or root in path.parents, f'{name} escapes APP_PERSIST_ROOT: {path}'
print(f'[Supervisor] Open WebUI DATA_DIR={Path(os.environ["DATA_DIR"]).resolve()}')
PY

# Debian does not place initdb or pg_ctl on PATH; always use pg_config's bindir.
chown -R postgres:postgres "$PGDATA"
if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
  log "Initializing PostgreSQL $PG_MAJOR at $PGDATA"
  runuser -u postgres -- "$PG_BINDIR/initdb" -D "$PGDATA" --auth-local=trust --auth-host=trust >/dev/null
fi
[[ "$(cat "$PGDATA/PG_VERSION")" == "$PG_MAJOR" ]] || {
  log "ERROR: PGDATA major $(cat "$PGDATA/PG_VERSION") does not match image major $PG_MAJOR"; exit 1;
}
runuser -u postgres -- "$PG_BINDIR/pg_ctl" -D "$PGDATA" \
  -o "-k /tmp -p $PGPORT -h 127.0.0.1" -w start >/dev/null
pg_isready -h 127.0.0.1 -p "$PGPORT" >/dev/null
psql -h 127.0.0.1 -p "$PGPORT" -U postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='plandex'" | grep -qx 1 || \
  psql -h 127.0.0.1 -p "$PGPORT" -U postgres -c 'CREATE DATABASE plandex' >/dev/null
export DATABASE_URL="${DATABASE_URL:-postgresql://postgres@127.0.0.1:$PGPORT/postgres}"
export PLANDEX_DATABASE_URL="${PLANDEX_DATABASE_URL:-postgresql://postgres@127.0.0.1:$PGPORT/plandex?sslmode=disable}"

# Plandex tokenizer, server reachability, and local auth are fail-closed gates.
start_child plandex env TIKTOKEN_CACHE_DIR="$PLANDEX_TIKTOKEN_CACHE_DIR" \
  bash "$repo_root/scripts/integration/start-plandex-server.sh"
env TIKTOKEN_CACHE_DIR="$PLANDEX_TIKTOKEN_CACHE_DIR" \
  bash "$repo_root/scripts/integration/bootstrap-plandex-local.sh"

# These preflights execute their established runtime contracts, rather than
# treating a directory as proof that a component is usable.
if [[ ! -x "$GRAPHIFY_EXECUTABLE" ]]; then
  GRAPHIFY_STATE=unavailable
elif "$GRAPHIFY_EXECUTABLE" --version | grep -Fx 'graphify 0.9.67'; then
  GRAPHIFY_STATE=ready
else
  GRAPHIFY_STATE=failed
fi
log "Graphify capability: $GRAPHIFY_STATE (degraded=$([[ $GRAPHIFY_STATE == ready ]] && echo false || echo true))"

if HF_HUB_OFFLINE=1 python3 "$repo_root/scripts/integration/needle-preflight.py"; then
  NEEDLE_STATE=ready
else
  NEEDLE_STATE=failed
fi
log "Needle capability: $NEEDLE_STATE (degraded=$([[ $NEEDLE_STATE == ready ]] && echo false || echo true))"

# Remote Spark/JIT integrations may degrade; their local adapters are managed
# but failures do not weaken the critical PostgreSQL/Plandex/WebUI gates.
SPARK_CONFIGURED=false
if [[ -n "${SPARK_API_KEY:-}" && -n "${SPARK_BASE_URL:-}" ]]; then
  SPARK_CONFIGURED=true
  start_child spark-adapter bash "$repo_root/scripts/integration/start-spark-contract.sh"
  spark_pid=${CHILD_PIDS[${#CHILD_PIDS[@]}-1]}
  start_child headroom bash "$repo_root/scripts/integration/start-headroom.sh"
  headroom_pid=${CHILD_PIDS[${#CHILD_PIDS[@]}-1]}
  if HEADROOM_READY_TIMEOUT="${HEADROOM_READY_TIMEOUT:-30}" bash "$repo_root/scripts/integration/wait-headroom.sh" &&
     SPARK_CONTRACT_READY_TIMEOUT="${SPARK_CONTRACT_READY_TIMEOUT:-30}" bash "$repo_root/scripts/integration/wait-spark-contract.sh"; then
    HEADROOM_STATE=ready
    SPARK_STATE=ready
  else
    HEADROOM_STATE=failed
    SPARK_STATE=unavailable
    stop_child "$headroom_pid"
    stop_child "$spark_pid"
    log 'Spark path unavailable; baseline runtime will continue'
  fi
else
  log 'Spark path not configured; Headroom and Spark adapter will not be started'
fi
start_child open-webui bash "$repo_root/backend/start.sh"
webui_pid=${CHILD_PIDS[${#CHILD_PIDS[@]}-1]}

# Wait for the public listener and then enforce the complete binding contract.
for _ in $(seq 1 120); do
  ss -H -ltn | awk '$4 == "0.0.0.0:7860" {found=1} END {exit !found}' && break
  kill -0 "$webui_pid" 2>/dev/null || { log 'ERROR: Open WebUI exited during startup'; exit 1; }
  sleep 1
done
ss -H -ltn | awk '$4 == "0.0.0.0:7860" {found=1} END {exit !found}' || {
  log 'ERROR: Open WebUI did not bind 0.0.0.0:7860'; exit 1;
}
ss -H -ltn | awk '$4 ~ /^0\.0\.0\.0:/ && $4 != "0.0.0.0:7860" {print; bad=1} END {exit bad}' || {
  log 'ERROR: an unexpected service is publicly bound'; exit 1;
}
for endpoint in 127.0.0.1:5432 127.0.0.1:8099; do
  ss -H -ltn | awk -v endpoint="$endpoint" '$4 == endpoint {found=1} END {exit !found}' || {
    log "ERROR: required local listener missing: $endpoint"; exit 1;
  }
done
if [[ "$SPARK_CONFIGURED" == true && "$SPARK_STATE" == ready ]]; then
  for endpoint in 127.0.0.1:8787 127.0.0.1:8790; do
    if ! ss -H -ltn | awk -v endpoint="$endpoint" '$4 == endpoint {found=1} END {exit !found}'; then
      SPARK_STATE=failed
      HEADROOM_STATE=failed
      log "Spark path degraded: optional local listener missing: $endpoint"
    fi
  done
fi
write_readiness
log 'Runtime binding contract verified; coding runtime ready'

# No restart loop: any critical child exit makes the container unready and
# initiates one bounded graceful shutdown.
while kill -0 "$webui_pid" 2>/dev/null && kill -0 "${CHILD_PIDS[0]}" 2>/dev/null; do
  pg_isready -h 127.0.0.1 -p "$PGPORT" >/dev/null || { log 'ERROR: PostgreSQL exited'; exit 1; }
  sleep 2
done
log 'ERROR: a critical service exited'
exit 1

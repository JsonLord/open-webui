#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Integration Process Supervisor & Startup Entrypoint
# Opens Open WebUI Coding Runtime, manages persistent volume layout,
# boots PostgreSQL, Plandex server, Headroom, Spark contract adapter,
# preflights Needle/Graphify/tiktoken, and launches Open WebUI.
# ==============================================================================

export APP_PERSIST_ROOT="${APP_PERSIST_ROOT:-/data/agent-platform}"
export PORT="${PORT:-7860}"
export HOST="${HOST:-0.0.0.0}"
export PATH="/opt/integration/bin:${PATH}"
export TIKTOKEN_CACHE_DIR="${TIKTOKEN_CACHE_DIR:-/opt/integration/tiktoken-cache}"

echo "=================================================="
echo "Open WebUI Coding Runtime starting"
echo "Persistent root: ${APP_PERSIST_ROOT}"
echo "Plandex CLI: $(command -v plandex || echo '/opt/integration/bin/plandex')"
echo "Plandex Server: $(command -v plandex-server || echo '/opt/integration/bin/plandex-server')"
echo "GitHub MCP Server: $(command -v github-mcp-server || echo '/opt/integration/bin/github-mcp-server')"
echo "Graphify: /data/agent-platform/graphify"
echo "Needle: /opt/integration/needle"
echo "Headroom: 127.0.0.1:8787"
echo "Spark adapter: 127.0.0.1:8790"
echo "=================================================="

# 1. Prepare persistent storage layout
mkdir -p "${APP_PERSIST_ROOT}/postgres" \
         "${APP_PERSIST_ROOT}/plandex-server" \
         "${APP_PERSIST_ROOT}/plandex-cli" \
         "${APP_PERSIST_ROOT}/open-webui" \
         "${APP_PERSIST_ROOT}/control-plane" \
         "${APP_PERSIST_ROOT}/repositories" \
         "${APP_PERSIST_ROOT}/graphify"

export PLANDEX_BASE_DIR="${APP_PERSIST_ROOT}/plandex-server"
export PLANDEX_CLI_HOME="${APP_PERSIST_ROOT}/plandex-cli"

# 2. PostgreSQL Runtime Management
PGDATA="${APP_PERSIST_ROOT}/postgres"
PGPORT=5432

# Ensure postgres user owns PGDATA
if id "postgres" &>/dev/null; then
  chown -R postgres:postgres "${PGDATA}"
  RUN_PG="su - postgres -c"
else
  RUN_PG="bash -c"
fi

if [[ ! -f "${PGDATA}/PG_VERSION" ]]; then
  echo "[Supervisor] Initializing PostgreSQL database cluster at ${PGDATA}..."
  ${RUN_PG} "initdb -D '${PGDATA}' --auth-local=trust --auth-host=trust" > /dev/null
fi

echo "[Supervisor] Starting PostgreSQL server on 127.0.0.1:${PGPORT}..."
${RUN_PG} "pg_ctl -D '${PGDATA}' -o '-k /tmp -p ${PGPORT} -h 127.0.0.1' start" > /dev/null

echo "[Supervisor] Waiting for PostgreSQL readiness..."
until pg_isready -h 127.0.0.1 -p "${PGPORT}" > /dev/null 2>&1; do
  sleep 1
done

# Ensure database and users exist
psql -h 127.0.0.1 -p "${PGPORT}" -U postgres -tc "SELECT 1 FROM pg_database WHERE datname = 'plandex'" | grep -q 1 || \
  psql -h 127.0.0.1 -p "${PGPORT}" -U postgres -c "CREATE DATABASE plandex;" > /dev/null

# Set DATABASE_URL for Open WebUI & Control Plane if not set
export DATABASE_URL="${DATABASE_URL:-postgresql://postgres@127.0.0.1:${PGPORT}/postgres}"
export PLANDEX_DATABASE_URL="${PLANDEX_DATABASE_URL:-postgresql://postgres@127.0.0.1:${PGPORT}/plandex?sslmode=disable}"

# 3. Start Plandex Server
echo "[Supervisor] Starting Plandex server..."
if [[ -f "/app/scripts/integration/start-plandex-server.sh" ]]; then
  bash /app/scripts/integration/start-plandex-server.sh &
elif [[ -f "scripts/integration/start-plandex-server.sh" ]]; then
  bash scripts/integration/start-plandex-server.sh &
else
  plandex-server > /tmp/plandex-server.log 2>&1 &
fi

# Wait for Plandex server and execute local auth/tokenizer bootstrap
echo "[Supervisor] Running Plandex readiness and auth bootstrap..."
if [[ -f "/app/scripts/integration/bootstrap-plandex-local.sh" ]]; then
  bash /app/scripts/integration/bootstrap-plandex-local.sh || echo "[Supervisor] WARNING: Plandex local bootstrap returned non-zero"
elif [[ -f "scripts/integration/bootstrap-plandex-local.sh" ]]; then
  bash scripts/integration/bootstrap-plandex-local.sh || echo "[Supervisor] WARNING: Plandex local bootstrap returned non-zero"
fi

# 4. Start Headroom proxy (127.0.0.1:8787)
echo "[Supervisor] Starting Headroom proxy on 127.0.0.1:8787..."
if [[ -f "/app/scripts/integration/start-headroom.sh" ]]; then
  bash /app/scripts/integration/start-headroom.sh &
elif [[ -f "scripts/integration/start-headroom.sh" ]]; then
  bash scripts/integration/start-headroom.sh &
fi

# 5. Start Spark contract adapter (127.0.0.1:8790)
echo "[Supervisor] Starting Spark contract adapter on 127.0.0.1:8790..."
if [[ -f "/app/scripts/integration/start-spark-contract.sh" ]]; then
  bash /app/scripts/integration/start-spark-contract.sh &
elif [[ -f "scripts/integration/start-spark-contract.sh" ]]; then
  bash scripts/integration/start-spark-contract.sh &
fi

# 6. Preflight checks for Needle / Graphify / Tiktoken
echo "[Supervisor] Verifying runtime preflights..."
python3 -c "
import os
assert os.path.exists('/opt/integration/tiktoken-cache/fb374d419588a4632f3f557e76b4b70aebbca790'), 'tiktoken cache missing'
print('[Supervisor] Tiktoken cache OK')
" || true

# 7. Launch Open WebUI via backend/start.sh
echo "[Supervisor] Launching Open WebUI frontend/backend on ${HOST}:${PORT}..."
export PORT="${PORT}"
export HOST="${HOST}"

if [[ -d "/app/backend" ]]; then
  cd /app/backend
fi

exec bash start.sh

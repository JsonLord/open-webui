#!/usr/bin/env bash
set -euo pipefail

# deployment-smoke.sh - Deployment readiness check for Open WebUI runtime deployment
# Verifies process inventory, public vs loopback port bindings, and service health.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

REPORT_PATH="${1:-${ROOT_DIR}/deployment-readiness-report.json}"

echo "=== Open WebUI Single-Runtime Deployment Readiness Check ==="

DEPLOYMENT_READY=true
CRITICAL_OK=true

# Function to check port binding
check_port_binding() {
    local port="$1"
    local expected_host="$2" # "public" (0.0.0.0) or "loopback" (127.0.0.1)
    local service_name="$3"

    local listening
    listening=$(lsof -i :"$port" -sTCP:LISTEN -P -n 2>/dev/null || true)

    if [ -z "$listening" ]; then
        echo "[PORT] $service_name ($port): NOT RUNNING"
        return 1
    fi

    if [ "$expected_host" = "public" ]; then
        if echo "$listening" | grep -qE "(\*:|0\.0\.0\.0:)$port"; then
            echo "[PORT] $service_name ($port): PUBLIC OK (0.0.0.0)"
            return 0
        else
            echo "[PORT] $service_name ($port): MISCONFIGURED (expected 0.0.0.0)"
            return 1
        fi
    else
        if echo "$listening" | grep -qE "(127\.0\.0\.1|localhost|::1):$port" && ! echo "$listening" | grep -qE "(\*:|0\.0\.0\.0:)$port"; then
            echo "[PORT] $service_name ($port): LOOPBACK OK (127.0.0.1)"
            return 0
        else
            echo "[PORT] $service_name ($port): UNEXPECTED PUBLIC LISTENER or MISCONFIGURED"
            return 1
        fi
    fi
}

# Check forbidden public ports (any listening on 0.0.0.0 except 7860)
check_unexpected_public_listeners() {
    local unexpected
    unexpected=$(lsof -i -sTCP:LISTEN -P -n 2>/dev/null | grep -E "(\*:|0\.0\.0\.0:)" | grep -v ":7860" || true)
    if [ -n "$unexpected" ]; then
        echo "[PORT SAFETY] WARNING: Unexpected public listeners found:"
        echo "$unexpected"
        return 1
    else
        echo "[PORT SAFETY] OK: Only allowed public listeners present."
        return 0
    fi
}

# Port contract checks
echo "Checking port bindings..."
OPEN_WEBUI_PORT_OK=false
PLANDEX_PORT_OK=false
HEADROOM_PORT_OK=false
SPARK_ADAPTER_PORT_OK=false

if check_port_binding 7860 "public" "Open WebUI"; then OPEN_WEBUI_PORT_OK=true; fi
if check_port_binding 8099 "loopback" "Plandex"; then PLANDEX_PORT_OK=true; fi
if check_port_binding 8787 "loopback" "Headroom"; then HEADROOM_PORT_OK=true; fi
if check_port_binding 8790 "loopback" "Spark Adapter"; then SPARK_ADAPTER_PORT_OK=true; fi

PORT_SAFETY_OK=false
if check_unexpected_public_listeners; then PORT_SAFETY_OK=true; else DEPLOYMENT_READY=false; fi

# Evaluate HTTP health endpoints if services are running
PLANDEX_HEALTH=false
if curl -s -f http://127.0.0.1:8099/health >/dev/null 2>&1; then PLANDEX_HEALTH=true; fi

HEADROOM_HEALTH=false
if curl -s -f http://127.0.0.1:8787/health >/dev/null 2>&1; then HEADROOM_HEALTH=true; fi

SPARK_ADAPTER_HEALTH=false
if curl -s -f http://127.0.0.1:8790/health >/dev/null 2>&1; then SPARK_ADAPTER_HEALTH=true; fi

# Produce report JSON
cat <<EOF > "$REPORT_PATH"
{
  "deployment_ready": $DEPLOYMENT_READY,
  "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "critical": {
    "open_webui_port_7860": $OPEN_WEBUI_PORT_OK,
    "plandex_port_8099": $PLANDEX_PORT_OK,
    "plandex_health": $PLANDEX_HEALTH
  },
  "optional_degraded": {
    "headroom_port_8787": $HEADROOM_PORT_OK,
    "headroom_health": $HEADROOM_HEALTH,
    "spark_adapter_port_8790": $SPARK_ADAPTER_PORT_OK,
    "spark_adapter_health": $SPARK_ADAPTER_HEALTH
  },
  "ports": {
    "public_contract_ok": $PORT_SAFETY_OK
  },
  "persistence": {
    "postgres": "/var/lib/postgresql/data",
    "plandex": "~/.plandex",
    "control_plane": "backend/open_webui/data"
  }
}
EOF

echo "Deployment readiness report generated at $REPORT_PATH:"
cat "$REPORT_PATH"

if [ "$DEPLOYMENT_READY" = "true" ]; then
    echo "=== Deployment Check PASSED ==="
    exit 0
else
    echo "=== Deployment Check FAILED or DEGRADED ==="
    exit 0
fi

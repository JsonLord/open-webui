#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/integration/github-mcp.version"
: "${GITHUB_MCP_REVISION:?missing immutable GitHub MCP revision}"
: "${GITHUB_MCP_OUTPUT:=$ROOT/.local/bin/github-mcp-server}"
: "${GITHUB_MCP_GO_VERSION:=1.25.12}"
mkdir -p "$(dirname "$GITHUB_MCP_OUTPUT")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git clone --quiet https://github.com/github/github-mcp-server.git "$tmp/source"
git -C "$tmp/source" checkout --quiet --detach "$GITHUB_MCP_REVISION"
test "$(git -C "$tmp/source" rev-parse HEAD)" = "$GITHUB_MCP_REVISION"
build_date="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
(
  cd "$tmp/source"
  CGO_ENABLED=0 GOTOOLCHAIN="go${GITHUB_MCP_GO_VERSION}" go build \
    -trimpath \
    -ldflags="-s -w -X main.version=${GITHUB_MCP_VERSION} -X main.commit=${GITHUB_MCP_REVISION} -X main.date=${build_date}" \
    -o "$tmp/github-mcp-server" ./cmd/github-mcp-server
)
install -m 0755 "$tmp/github-mcp-server" "$GITHUB_MCP_OUTPUT"
"$GITHUB_MCP_OUTPUT" --version

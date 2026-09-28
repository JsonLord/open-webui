from pathlib import Path


ROOT_DOCKERFILE = Path(__file__).resolve().parents[1] / 'Dockerfile'


def test_native_builders_use_component_supported_go_toolchains():
    dockerfile = ROOT_DOCKERFILE.read_text(encoding='utf-8')

    assert 'FROM golang:1.23-bookworm AS integration-go-builder' in dockerfile
    assert 'FROM golang:1.25.12-bookworm AS github-mcp-builder' in dockerfile
    assert 'COPY --from=github-mcp-builder /github-mcp-server /opt/integration/bin/github-mcp-server' in dockerfile


def test_github_mcp_checkout_and_binary_are_verified():
    dockerfile = ROOT_DOCKERFILE.read_text(encoding='utf-8')

    assert 'test "$(git -C /tmp/github-mcp rev-parse HEAD)" = "${GITHUB_MCP_REVISION}"' in dockerfile
    assert '/github-mcp-server --version' in dockerfile

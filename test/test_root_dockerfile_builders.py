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


def test_frontend_heap_limit_is_scoped_to_the_production_build():
    dockerfile = ROOT_DOCKERFILE.read_text(encoding='utf-8')

    assert 'Frontend build Node heap limit: 4096 MB' in dockerfile
    assert 'NODE_OPTIONS="--max-old-space-size=4096" npm run build' in dockerfile
    assert 'ENV NODE_OPTIONS' not in dockerfile


def test_slim_build_installs_torch_from_the_cpu_index_first():
    dockerfile = ROOT_DOCKERFILE.read_text(encoding='utf-8')
    slim_branch = dockerfile.split('if [ "$USE_SLIM" = "true" ]; then', 1)[1]
    cpu_torch = (
        "pip3 install 'torch<=2.9.1' torchvision torchaudio "
        '--index-url https://download.pytorch.org/whl/cpu --no-cache-dir;'
    )

    assert cpu_torch in slim_branch
    assert slim_branch.index(cpu_torch) < slim_branch.index(
        'uv pip install --system -r requirements-slim.txt --no-cache-dir;'
    )

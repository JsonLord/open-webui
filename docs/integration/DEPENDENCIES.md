# Integration dependencies

| Upstream | Purpose | Version/tag/SHA | Integration | License | Update method |
|---|---|---|---|---|---|
| `open-webui/open-webui` | Browser UI/backend base | repository commit | Root source | Open WebUI license | Merge upstream deliberately |
| `plandex-ai/plandex` | Coding execution engine | imported at merge commit `ee017d58` (upstream SHA not recorded) | Copied tracked source under `plandex/` | MIT | Reconcile against upstream and record SHA before update |
| `JsonLord/headroom` | Compression proxy | package `headroom-ai[proxy]==0.37.0`; audited repo SHA `aa4739e54ad46d48dd829f730af359c4824c04c4` | Pinned Python runtime package | Apache-2.0 | Update pin, inspect changelog, rerun live proxy tests |
| `XHToken/Spark-X2.5-1.7B` | Remote Spark inference | `spark-x2.5-1.7b`; Q8_0 `Spark-X2.5-1.7B-Q8_0.gguf` | OpenAI-compatible Space at `leon4gr45-llama.hf.space` | Model terms TBD | Deployment owner updates endpoint; contract changes require ADR/test update |
| `Cactus-Compute/needle` / `Cactus-Compute/needle3` | Local tool-selection model/runtime | package `cactus-needle==3.0.1`; engine `3.0.1`; inspected source SHA `42bf1f2d0a7784b0d4d1ec94bb5ade425cf9a67c`; model revision `0f51a1ac2917a03644c4cc7836f19476c6d177dd` | Build-baked `needle3.cact` plus `manylinux2014_x86_64` engine in native cache | Apache-2.0 | Rebuild from immutable pins, run offline/evaluation tests, and record extracted-library diagnostic hash |
| `github/github-mcp-server` | GitHub issue/PR/check context | v1.12.2, commit `85598ba6e1256f7ebf4867b95d63b833c4549264`, Go `1.25.12` | Build-stage compiled binary at `/opt/integration/bin/github-mcp-server`; external read-only stdio process; `repos,issues,pull_requests,actions` | MIT | Update immutable pin, rebuild binary, rerun read-only smoke |
| `Graphify-Labs/graphify` | Local structural repository graph | v0.9.67 / `4c735618f3d56fd622c2049771584621c31ba9ff` | Isolated `graphifyy` CLI; code-only `graph.json` | Apache-2.0 | Update immutable source/package pins together and rerun adapter/smoke tests |
| `bingreeky/JIT` | Future cloud planning adapter | not pinned | Not implemented | MIT | Later phase |
| `alibaba/open-code-review` | Future review framework | not pinned | Not implemented | Apache-2.0 | Later phase |
| `OpenBMB/MiniCPM` | Future remote review inference | not pinned | Not implemented | Apache-2.0 repository code | Later phase |

Headroom's package pin is intentionally separate from Open WebUI's application
dependencies so the baseline web application is not silently coupled before a
unified HF runtime image and supervisor are implemented.

The Spark contract adapter has no new runtime package dependency; it uses the
Python standard library. The remote GGUF absolute cache path is informational
and is deliberately not a client dependency. Its transport tests also use only
the standard library and a process-local fake upstream; no mock model package
or alternate inference runtime is introduced.

## Needle3 artifact contract

Installed `cactus-needle==3.0.1` was inspected directly, not inferred from the
newer default branch:

| Property | Installed value |
|---|---|
| Generation | `3` |
| Engine version | `3.0.1` |
| Repository | `Cactus-Compute/needle3` |
| Base archive | `needle3.cact` |
| Cache | `~/.cache/cactus-needle/v3/3.0.1` |
| HF Linux target | `manylinux2014_x86_64` (glibc) |
| Engine artifact | `python/cactus_needle-3.0.1-py3-none-manylinux2014_x86_64.whl` |
| Wheel member | `needle/libneedle3.so` |
| Cached library | `libneedle.so` |
| Immutable HF revision | `0f51a1ac2917a03644c4cc7836f19476c6d177dd` |
| Model SHA256 | `c9d915eca282ed42d1a09b143b592adb4cc6744ffe2d294adf5cfc5548170c38` |
| Engine wheel SHA256 | `05770ef9a85686583968ea15f62f9ad44217e078efdaa99559d3208bb8a369b0` |
| Extracted library SHA256 | not obtained; optional diagnostic pin |

The build fetcher uses the immutable revision and verifies the wheel before extraction. The extracted shared-library checksum has distinct optional naming and is never confused with the published wheel checksum.

## Phase 3D runtime dependency findings

- PostgreSQL 16.15 was provisioned locally and verified with the production `PostgresTaskStore`; the URL is documented only as `postgresql://<user>@127.0.0.1:5432/<database>`.
- GitHub MCP's pinned `go.mod` declares Go 1.25.12. `integration/Dockerfile.github-mcp` uses that exact builder and copies only the compiled binary into its runtime stage.
- Plandex server/CLI source builds succeeded with Go 1.24.3, but runtime initialization requires `o200k_base.tiktoken` from `openaipublic.blob.core.windows.net`. That artifact is not yet pinned/baked and its request received CONNECT proxy HTTP 403.

## Plandex tokenizer artifact

- **Dependency:** `github.com/pkoukk/tiktoken-go v0.1.7` (resolved by current Plandex Go modules)
- **Purpose:** `EncodingForModel("gpt-4o")` / `o200k_base` token counting during Plandex initialization
- **Canonical source:** `https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken`
- **SHA256:** `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`
- **URL cache key (SHA-1):** `fb374d419588a4632f3f557e76b4b70aebbca790`
- **Pinned behavior inspected:** v0.1.7 honors `TIKTOKEN_CACHE_DIR`, keys files as SHA-1(source URL), returns an existing cache file without content verification, and otherwise downloads then renames a temporary file
- **Integration:** verified build-time download into `/opt/integration/tiktoken-cache`; no runtime download
- **Update:** verify a new canonical asset independently, update the lock and tests together, then rebuild

### Pinned tokenizer transport mirror

- **Repository:** `rmusser01/tldw_chatbook`
- **Commit:** `b0dadf19414f5f8faf69b854d8e59007d275083e`
- **Path:** `tldw_chatbook/assets/tiktoken_cache/fb374d419588a4632f3f557e76b4b70aebbca790`
- **Role:** transport only; the canonical OpenAI URL and canonical SHA256 remain authoritative
- **Observed artifact:** 3,613,922 bytes, SHA256 `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`

## Plandex local runtime

- **PostgreSQL:** 16.15, dedicated `plandex` database; native Plandex migrations
- **LiteLLM:** upstream-pinned `1.72.6` from `plandex/app/scripts/litellm_deps.sh`; required by server startup
- **CLI extension:** native local-only sign-in/validation and structured current-plan output in copied source
- **Persistent state:** dedicated server base directory plus private deployment-managed CLI home
- **Update:** rebase the small auth/current/bind-host patch when refreshing copied Plandex and rerun native auth/plan lifecycle tests

## Graphify structural graph runtime

- **Upstream/package:** `Graphify-Labs/graphify`, official package `graphifyy==0.9.67`
- **Immutable source:** tag `v0.9.67`, commit `4c735618f3d56fd622c2049771584621c31ba9ff`
- **License:** Apache-2.0 (repository also carries MIT notice for applicable portions)
- **Interface:** local CLI `graphify extract <source> --out <staging> --code-only --no-cluster`; persistent native `graphify-out/graph.json`, `manifest.json`, `.graphify_root`, and cache
- **Purpose:** deterministic AST structure, including files/symbols and supported `contains`, `calls`, import/reference/inheritance-style relationships
- **Default languages inspected:** the pinned package declares tree-sitter grammars for Python, JavaScript/TypeScript, Go, Rust, Java/Groovy, C/C++, Ruby, C#, Kotlin, Scala, PHP, Swift, Lua, Zig, PowerShell, Elixir, Objective-C, Julia, Verilog, Fortran, Bash, and JSON; other parsers are optional extras and are not baseline claims
- **Incremental behavior:** upstream supports manifest-based update/extract reuse, but the control-plane base index is immutable per Git SHA; dirty task worktrees are marked stale in this phase
- **Isolation:** separate build/runtime dependency file; code-only extraction has no LLM/API dependency and receives no application credentials
- **Update:** inspect the exact tagged CLI/schema, update source/package pins together, rebuild, and rerun graph adapter plus real smoke tests

### Graphify Phase 4B query/privacy behavior

Graphify 0.9.67 supports `GRAPHIFY_QUERY_LOG_DISABLE=1`; its `querylog.py` gives
this setting precedence over opt-in `GRAPHIFY_QUERY_LOG` and
`GRAPHIFY_QUERY_LOG_ENABLE`. The control-plane environment sets the disable flag
explicitly. Phase 4B does not invoke Graphify's natural-language query CLI at
all: it uses the pinned local `graph.json` through `RepositoryGraphService`, so
no full objective or response is written to the upstream query log. No Graphify
semantic, document, media, embedding, or LLM extra is configured.

The copied Plandex source has a small deployment extension: named piped context
(`load --name`) avoids its model-assisted name generation and `ls --json`
provides stable verification. This reuses the native server context API and
database; no context JSON or Plandex row is externally fabricated.

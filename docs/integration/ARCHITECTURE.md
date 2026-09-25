# Integration architecture

## Current architecture

The repository is an Open WebUI source tree with the Plandex repository copied
into the tracked `plandex/` directory. It is not a submodule or a nested Git
checkout. There is currently no runtime control-plane connection between the
two applications.

Open WebUI consists of the Svelte frontend under `src/`, the FastAPI backend
under `backend/open_webui/`, and its existing Docker build. The normal image
listens on port 8080 and uses Open WebUI's own persistence configuration.
Plandex retains its Go CLI, server, shared packages, migrations, and PostgreSQL
Docker Compose setup under `plandex/app/`.

Phase 1 integration assets now define this isolated path:

```text
Plandex custom provider `spark-via-headroom`
  -> http://127.0.0.1:8787/v1
  -> Headroom 0.37.0 proxy
  -> Spark request contract http://127.0.0.1:8790/v1
  -> SPARK_BASE_URL (deployment environment)
  -> remote Spark (`spark-x2.5-1.7b`)
```

`scripts/integration/start-headroom.sh` starts only proxy mode and
`scripts/integration/wait-headroom.sh` gates callers on process liveness. The deployment
token may arrive as `SPARK_API_TOKEN`, but is normalized to the canonical
`SPARK_API_KEY`. No secret is stored in the repository. Headroom's local stats
are enabled while its outbound beacon and update check are disabled. The
request-contract service validates the compressed request, applies the
32,768-token hard limit (24,576 input + 4,096 output + 4,096 reserve), sanitizes
parameters, applies bounded transient retries, and exposes aggregate metrics.
The physical ceiling cannot be raised by environment configuration. Successful
completion responses include `X-Spark-Estimated-Input-Tokens`; metrics also
record final serialized request bytes and aggregate upstream latency.
Its conservative fallback estimate is UTF-8 bytes divided by three plus message
framing; it intentionally overestimates typical English/code and is not called
an exact tokenizer count.

Phase 2 adds an isolated `integration/tool_router.py` library. It accepts an
intent and no more than five context-selected tool schemas, calls local Needle3
through `complete()` only, then applies JSON Schema and policy validation. It
does not execute anything. `ToolGateway` is a separate deterministic handler
registry and accepts only an approved `selected` decision. The router is not yet
wired to a control plane because that component does not exist.

The stock runtime is in-process and omits `weights=`. The installed 3.0.1
package caches `libneedle.so` and `needle3.cact` under
`~/.cache/cactus-needle/v3/3.0.1`; explicit `NEEDLE_CUSTOM_WEIGHTS` is a separate
future tuned-model path that launches the upstream worker subprocess. A glibc
`manylinux2014_x86_64` build fragment fetches and verifies both stock artifacts
during image construction, then runs offline preflight. Runtime status is an
internal path-free object, not a public service.

### Existing Plandex capabilities (do not duplicate)

| Capability | Existing location |
|---|---|
| Project maps / context maps | `plandex/app/server/handlers/file_maps*.go`, `plandex/app/server/db/context_helpers_map.go` |
| Smart/automatic context | `plandex/app/cli/lib/context_auto_load.go`, `plandex/app/server/model/` |
| Planner, architect, coder | `plandex/app/server/model/plan/`, `plandex/app/server/model/architect/` |
| Builder / whole-file builder | `plandex/app/server/model/build/` |
| Pending changes | `plandex/app/server/handlers/plans_changes.go`, `plandex/app/server/db/result_helpers.go` |
| Apply/revert/rewind | `plandex/app/cli/lib/apply.go`, `plandex/app/cli/lib/rewind.go` |
| Command execution / auto-debug | `plandex/app/cli/plan_exec/`, `plandex/app/cli/cmd/debug.go` |
| Plan creation / execution | `plandex/app/cli/cmd/plan_start_helpers.go`, `plan_exec_helpers.go`, server `handlers/plans_exec.go` |
| Custom providers / model packs | `plandex/app/shared/ai_models_custom.go`, `ai_models_packs.go`, CLI model commands |
| Git integration | `plandex/app/cli/lib/git.go`, `plandex/app/server/db/git.go` |

### Legacy audit

The old local-model design exists only in `spec_with_llama_swap.md`; no
llama-swap, llama-server, GGUF download, or local Spark/MiniCPM startup was
implemented in the current root Dockerfile or startup scripts. That document is
classification **C (obsolete baseline configuration)** and is retained as
historical context. Open WebUI's generic llama.cpp compatibility is
classification **A (still useful)** and unrelated to the coding-worker baseline.

## Target architecture

```text
browser -> Open WebUI 0.0.0.0:7860
              |
              v
       Control Plane (future)
          |             |
          |             +-> ToolRouter -> local Needle3
          |                    |
          |                    v
          |               ToolGateway
              |
              v
       Plandex 127.0.0.1:8099 -> PostgreSQL (internal/persistent)
              |
              v
       Headroom 127.0.0.1:8787
              |
              v
       Spark contract 127.0.0.1:8790
              |
              v
       Remote Spark (SPARK_BASE_URL)
```

The current architecture includes local Needle3 contracts, GitHub MCP, and the
Phase 4 Graphify foundation. Later phases add JIT/cloud planning, Open Code
Review, and remote MiniCPM without moving Plandex's execution responsibilities
into the control plane. Runtime state and model caches will
live outside user Git worktrees. Only port 7860 will be exposed publicly.

## Phase 3 control plane (current structural implementation)

Open WebUI's authenticated FastAPI backend mounts `/api/v1/control-plane` and
owns lifecycle APIs. A dedicated `control_plane` PostgreSQL schema stores tasks
and ordered events. The module authorizes repositories before deterministic Git
commands create a bare cache and one contained worktree per task. It calls the
native Plandex CLI through a thin adapter and the pinned official GitHub MCP
server through read-only stdio; neither implementation is duplicated.

```text
Open WebUI :7860 /api/v1/control-plane
  -> PostgreSQL control_plane.tasks + task_events
  -> git cache -> isolated agent/<slug>-<id> worktree
  -> GitHub MCP (stdio, read-only, four toolsets)
  -> Plandex native CLI/server boundary
       -> Headroom :8787 -> Spark contract :8790 -> remote Spark
```

The API supplies task CRUD/cancellation, resumable SSE event streams, diffs, repository
allowlist visibility, branches, and component health. It exposes neither raw
MCP calls nor ToolGateway execution. Worktrees remain after cancellation for
diagnosis; later cleanup must be explicit.

## Phase 3D observed runtime

The real Open WebUI process used PostgreSQL 16.15 at a redacted URL shape of
`postgresql://<user>@127.0.0.1:5432/<database>`. The production store created
`control_plane.tasks`, `control_plane.task_events`, and
`control_plane.repositories`. Restart, concurrent sequencing, and idempotency
were verified against that database. Startup conservatively fails every
interrupted non-terminal task with `recovery_required`.

GitHub MCP v1.12.2 was compiled at its pinned commit using exact Go 1.25.12 and
installed at `/opt/integration/bin/github-mcp-server`. Real stdio initialize and
tools/list succeeded with a recognizable fake token; 25 read-only tools from
the configured four toolsets were exposed. No authenticated GitHub API call was
made because a real PAT was unavailable.

A real authenticated API task cloned `JsonLord/open-webui`, resolved
`integration-plandex` to `9a67af27865d86427546bdacf852136f7d54a812`, and
created a contained `agent/...` worktree. The actual Plandex server and CLI were
built, but both panic during package initialization when the proxy rejects the
required `o200k_base.tiktoken` download. The task consequently failed as
`plandex_unavailable`; no plan, model execution, commit, push, or PR is claimed.


## Plandex tokenizer cache

The image-build path verifies `o200k_base.tiktoken` by canonical SHA256 and
installs it under tiktoken-go's canonical-URL SHA-1 key in
`/opt/integration/tiktoken-cache`. The cache is read-only at runtime. The
control-plane Plandex adapter preflights it and passes `TIKTOKEN_CACHE_DIR` to
every child; a build-only mirror may supply bytes but cannot change identity.

### Tokenizer mirror transport

The tokenizer build stage defaults to a raw URL pinned to
`rmusser01/tldw_chatbook@b0dadf19414f5f8faf69b854d8e59007d275083e`.
It observed the canonical 3,613,922-byte artifact and canonical SHA256. The
mirror supplies bytes only; runtime placement and identity remain tied to the
canonical OpenAI URL.

## Verified local Plandex runtime

PostgreSQL hosts a dedicated `plandex` database separate from Open WebUI and the
`control_plane` schema. Plandex server binds `127.0.0.1:8099`, persists native
repositories below `PLANDEX_BASE_DIR`, and uses its pinned LiteLLM proxy.
Deployment preflights tokenizer, server health, and native local auth before task
acceptance. `PLANDEX_CLI_HOME` is shared only as process HOME, never copied into
a worktree. Structured current-plan identity flows from native CLI JSON into the
durable task payload/event.

## Phase 4 current architecture — structural repository graph

```text
authorized owner/repo -> cached Git repository -> exact base commit/worktree
                                              -> RepositoryGraphService
                                              -> Graphify 0.9.67 code-only CLI
                                              -> persistent graph.json + manifest
                                              -> normalized local queries
```

Git/control-plane remains authoritative for repository authorization, identity,
revision, branches, worktrees, and cleanup. Graphify owns only its structural
artifact and relationship semantics. Plandex remains authoritative for plans
and coding execution. No model or inference service participates in indexing.

`RepositoryGraphService` accepts only the repository identity and source path
resolved by the repository lifecycle. Its deterministic identity covers
canonical repository, full commit SHA, Graphify version, and adapter schema.
The storage layout is:

```text
$CONTROL_PLANE_DATA_DIR/graphs/
  <sha256(repository)[:20]>/
    <full-commit-sha>/
      graphify-0.9.67-v1/
        control-plane-manifest.json
        graphify-out/graph.json
        graphify-out/manifest.json
        graphify-out/cache/
```

The root is deployment-owned and mode 0700 where created. Index data survives
control-plane, Plandex, and worker restarts and is never placed in a task
worktree. A process lock plus advisory file lock serializes same-index builds;
a staging directory is atomically promoted only after JSON validation.

The first increment uses synchronous indexing inside the existing background
task worker immediately after repository preparation. It does not add a second
scheduler. Failure records `FAILED` and a privacy-safe event but leaves task,
Git, and Plandex state intact. A base index is `STALE` as soon as its worktree
has any tracked/untracked change or no longer points at its recorded commit.

Normalized queries are `find_symbols`, `neighborhood`, `dependencies`,
`dependents`, and `summary`. Public metadata is limited to availability, state,
revision, stale flag, opaque deterministic index ID, and bounded counts. Source
paths, raw CLI output, query text, and credentials are excluded from health and
events. Graph output is not yet injected into Plandex context.

## Phase 4B — bounded Graphify context enrichment

```text
normalized TaskInput
  -> explicit path/symbol anchors
  -> READY graph for exact repository + base SHA (clean worktree only)
  -> GraphContextEnricher (<=3 local normalized queries)
  -> deduplicated/ranked GraphContext
  -> <=12 KiB / <=2,500 estimated-token structural briefing
  -> native plan: plandex load --name structural-context-<hash> via stdin
  -> plandex ls --json verification
  -> Plandex smart/auto context remains authoritative
```

`RepositoryGraphService` still owns only index lifecycle and normalized graph
operations. `GraphContextEnricher` owns query policy, ranking, safe paths,
budgets, provenance, and deterministic rendering. `PlandexExecutor` owns bounded
stdin, native load/list verification, child lifecycle, and the private CLI
credential environment. No Graphify MCP, Needle routing, source-file auto-load,
`plandex tell`, or model inference is part of this boundary.

The task payload persists the graph-context hash, bytes, estimated tokens, node,
relationship and file counts, loaded timestamp, status, truncation, and whether
the base-revision evidence is still current. It never persists the rendered
brief. Events use the same bounded counts, a hash prefix, durations, and outcome;
objectives and briefing text never enter SSE. Identical loaded hashes skip a
second native load, while native `ls --json` independently prevents duplicates.

Graphify query logging is opt-in upstream in 0.9.67, and the subprocess
environment additionally sets the supported `GRAPHIFY_QUERY_LOG_DISABLE=1`.
The structural queries in this implementation read the private graph artifact
through the existing adapter, so no query string is sent to Graphify CLI or any
remote service.

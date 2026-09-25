# Agentic coding platform architecture specification

This file supersedes `spec_with_llama_swap.md`, which is retained only as a
historical record. The baseline is Open WebUI + Plandex + Headroom + **remote**
Spark. It must not require llama.cpp, llama-swap, local Spark weights, or local
MiniCPM weights.

## Responsibility boundaries

- Open WebUI is the browser UI.
- A future control plane owns lifecycle, scheduling, APIs, GitHub lifecycle,
  and orchestration.
- Plandex remains the coding execution engine and owns repository context,
  planning/execution, builders, pending changes, apply/revert, testing, and
  debugging.
- Headroom is the local context-compression proxy.
- Remote Spark is the coding/reasoning model.
- In a later phase, Needle3 is the only model required to run locally and may
  select tools and arguments but may never execute tools.
- GitHub MCP, Graphify, JIT/cloud planning, Open Code Review, and remote MiniCPM
  are later phases.

## Phase 0 and Phase 1

The first milestone is:

```text
Plandex -> http://127.0.0.1:8787/v1 -> Headroom
        -> http://127.0.0.1:8790/v1 -> Spark request contract
        -> SPARK_BASE_URL -> remote Spark -> valid response
```

Configuration is deployment supplied through `SPARK_BASE_URL`, `SPARK_MODEL`,
and `SPARK_API_KEY`. `SPARK_API_TOKEN` is a temporary accepted secret alias.
The Spark model ID and precise API contract must be discovered by probing the
service, never guessed. Headroom runs in proxy mode, preserves the requested
model, and does not enable Serena, memory, learn, wrap, or MCP integrations.

The public HF Docker Space application will ultimately bind Open WebUI to
`0.0.0.0:7860`; Plandex defaults to `127.0.0.1:8099`, Headroom to
`127.0.0.1:8787`, and PostgreSQL remains internal. Only port 7860 is public.

Do not begin Needle3 or later integrations until the real
Plandex-through-Headroom-through-Spark inference chain is verified.

## Remote Spark Request Contract

The remote model is `spark-x2.5-1.7b`, from the
`XHToken/Spark-X2.5-1.7B` family. The remote Space currently serves
`Spark-X2.5-1.7B-Q8_0.gguf` (Q8_0). Its remote filesystem path is operational
metadata only and must never be referenced by this application.

The physical context ceiling is 32,768 tokens. Ordinary requests use a 24,576
input ceiling, a 4,096 maximum output, and a 4,096 reserve. The final,
post-Headroom request must satisfy:

```text
estimated_input_tokens + requested_output_tokens + context_reserve <= 32768
```

The reserve protects chat-template expansion, system additions, message
framing, tokenizer estimation error, continuation, and repair turns. The
adapter must also reject input above 24,576 tokens even if a smaller output
would technically fit. It clamps output to 4,096 rather than expanding the
normal budget.

The only accepted generation fields are `model`, `messages`, `stream`,
`max_tokens` (or the normalized alias `max_completion_tokens`), `temperature`,
`top_p`, and `stop`. Defaults are temperature 0.2, top-p 0.95, and 4,096 output
tokens. All values and budgets are environment-configurable, but the configured
model must match `spark-x2.5-1.7b`. A deployment may lower the effective context
limit but may never configure it above the model's 32,768-token physical ceiling.

The request path is strictly:

```text
Plandex -> Headroom :8787 -> Spark contract :8790 -> remote Spark
```

Plandex selects context, Headroom compresses it, and the contract validates the
final request. The contract uses a documented conservative UTF-8 byte estimate
when an exact cached Spark tokenizer is unavailable; it does not perform a
network tokenizer lookup per request or claim that the estimate is exact.
The adapter reports its final estimate in a response header and records final
serialized request bytes and Spark latency. Headroom separately owns the
before/after compression counts and compression ratio; operators correlate the
two layers rather than making the contract adapter recompress the request.

Connect and read timeouts default to 15 and 300 seconds. At most one retry is
performed by default, only before response streaming begins and only for
connection failures or HTTP 502/503/504. Authentication failures, other 4xx
responses, schema errors, and context overflow are never retried.

An oversized post-compression prompt is never sliced or silently truncated. It
fails with `spark_context_budget_exceeded` and token-budget metadata. The caller
must instead remove duplicate tool/log output, reduce smart context, summarize
history, or split execution. Later JIT may replan such work.

## Local Needle3 ToolRouter

Needle3 (`Cactus-Compute/needle3`) is the only baseline local AI model. Its
scope is limited to selecting one tool from a small, task-derived candidate set,
producing typed arguments and calibrated confidence, or abstaining. It is not a
planner, coding model, orchestrator, shell, GitHub controller, or tool executor.

```text
Plandex / future Control Plane
  -> action intent + at most five candidate schemas
  -> ToolRouter
  -> local Needle3 `complete()` (never `run()`)
  -> JSON Schema validation
  -> role/effect/confirmation policy validation
  -> selected | confirm | abstain | rejected
  -> ToolGateway deterministic registered handler (selected only)
```

Candidate selection is deterministic and occurs before Needle inference;
Needle never receives the global tool inventory. Unknown tools, invalid typed
arguments, forbidden roles/effects, malformed confidence, and over-large
candidate sets fail closed. The default policy permits only `read` effects.
Confidence at or above 0.70 may select a tool, 0.30–0.70 requires confirmation,
and lower or missing confidence abstains. Needle-suppressed calls require
confirmation. An empty call list is a normal abstention. The installed 3.0.1
envelope can also carry `validation.ungrounded` and `validation.negation`; either
signal rejects the proposal even when confidence is high.

The router uses the upstream single-turn `complete()` API and never the `run()`
API because upstream `run()` executes decorated Python functions. Only a
separate ToolGateway can invoke a pre-registered deterministic handler, and it
rejects every decision other than `selected`.

The inference-only `cactus-needle==3.0.1` package is pinned; the audited source
SHA is `42bf1f2d0a7784b0d4d1ec94bb5ade425cf9a67c`. The installed wheel reports
generation 3, engine version 3.0.1, repository `Cactus-Compute/needle3`, base
archive `needle3.cact`, cache `~/.cache/cactus-needle/v3/3.0.1`, and on the HF
glibc x86_64 target uses
`python/cactus_needle-3.0.1-py3-none-manylinux2014_x86_64.whl`, extracting
`needle/libneedle3.so` as cached `libneedle.so`.

Both artifacts are fetched at image build time from immutable HF revision
`0f51a1ac2917a03644c4cc7836f19476c6d177dd` and placed in that native cache
layout. `needle3.cact` is pinned to SHA256
`c9d915eca282ed42d1a09b143b592adb4cc6744ffe2d294adf5cfc5548170c38`.
The downloaded Linux wheel is verified *before extraction* against SHA256
`05770ef9a85686583968ea15f62f9ad44217e078efdaa99559d3208bb8a369b0`;
the extracted library hash is diagnostic until independently published or
calculated. Startup runs preflight with `HF_HUB_OFFLINE=1`; request handling
must never download artifacts. Artifact identity is resolved, but acquisition,
offline inference, sizes, memory, and latency remain unverified because the
environment's CONNECT proxy returns 403.

The stock baseline deliberately omits `weights=`. This selects upstream's
in-process base path, whose native-library and base-weight registries load once
per process. `NEEDLE_CUSTOM_WEIGHTS` is reserved for future tuned `.cact`
archives; setting it selects upstream's worker subprocess and reports
uncalibrated confidence. Independent stock decisions reconstruct the lightweight
schema session, which reinitializes native tool schema/history while retaining
the process-loaded library and base weights. No schema-instance cache is added
until real 1/3/5-candidate benchmarks show a benefit.

Candidate maximum remains five, maximum output remains 512, and the provisional
0.70 execute / 0.30 confirm thresholds remain unchanged pending the checked-in
30-case real-model evaluation. Needle telemetry is disabled in the baseline.


## GitHub-first control plane

The lifecycle control plane is an authenticated Open WebUI FastAPI backend
module mounted at `/api/v1/control-plane`; it is not another coding agent.
Production persistence uses the dedicated PostgreSQL `control_plane` schema
with durable tasks and ordered task events. The typed state machine permits
only validated transitions through `QUEUED`, repository/context/planning and
execution phases, validation/apply/commit/push/PR phases, terminal states, and
the explicit cancellation path. Every transition emits a sequence-numbered,
privacy-safe event; prompt bodies, credentials, and large source content are
not event payloads.

Repositories are normalized to credential-free GitHub `owner/repo` identities
and checked against `ALLOWED_GITHUB_REPOS` or `ALLOWED_GITHUB_ORGS` before any
clone. Deterministic Git commands maintain cached bare clones and contained
per-task worktrees on `agent/<slug>-<short-id>` branches. Cancellation stops
only a child process owned by that task and preserves its worktree. An
`Idempotency-Key` maps retries to the existing task.

The official GitHub MCP server is pinned at v1.12.2 / commit
`85598ba6e1256f7ebf4867b95d63b833c4549264`, runs as a local read-only stdio
process, and exposes only `repos,issues,pull_requests,actions`. `GITHUB_PAT` is
the canonical secret; aliases exist only in the child environment. Local Git
operations never use MCP. Issue responses are normalized and bounded before
becoming task context.

`PlandexExecutor` is a thin owner-scoped wrapper over native `plandex new`,
`tell`, `current`, `diff --plain`, and `apply`; it does not generate or edit
code. The authenticated API supplies health/status, create/list/get/cancel,
resumable SSE event streams, task diff, allowed repositories, and cached branches.
Health reports configured, reachable, and runtime-verified independently for
PostgreSQL, Plandex, Headroom, Spark contract, Needle, GitHub MCP, git, and gh.
GitHub MCP, PostgreSQL integration, and native Plandex lifecycle remain runtime
unverified in this environment. Task creation queues a single-process background
worker for the bounded issue/prompt -> worktree -> native `plandex new` path.
Plandex CLI human output is never misrepresented as its server plan UUID;
`plan_id` remains empty until a stable native identifier is available.


## Phase 3 runtime recovery and deployment verification

Production control-plane startup requires PostgreSQL and initializes the dedicated `control_plane` schema. Task creation plus its first event and every state transition plus its event are atomic. Per-task row locking serializes event sequence assignment. On restart, any non-terminal task is conservatively transitioned to `FAILED` with `last_error=recovery_required`; no operation is silently resumed or marked successful.

GitHub MCP v1.12.2 requires and is built with exact Go 1.25.12. The multi-stage build copies only `/opt/integration/bin/github-mcp-server` into the runtime. Real stdio `initialize`, `initialized`, and `tools/list` were verified in read-only mode with the four configured toolsets. Live GitHub authentication remains unverified without `GITHUB_PAT`.

The authenticated Open WebUI control-plane API, PostgreSQL-backed resumable SSE, repository authorization, real GitHub clone/fetch, branch creation, worktree containment, restart persistence, concurrency, cancellation idempotency, and normalized failure were runtime verified. A real task resolved `JsonLord/open-webui` branch `integration-plandex` to `9a67af27865d86427546bdacf852136f7d54a812`.

The tokenizer cache, local Plandex server, non-interactive native local auth, `plandex new`, `plandex current --json`, structured plan identity persistence, and restart agreement are verified. Phase 3 local runtime is closed. Spark-backed execution, commit, push, and PR creation remain independently unverified or unimplemented and are not prerequisites for the Phase 4 local graph foundation.

## Plandex tokenizer deployment contract

Plandex is pinned to `github.com/pkoukk/tiktoken-go v0.1.7` and initializes
`EncodingForModel("gpt-4o")`, which requires the `o200k_base` encoding. The
canonical asset is
`https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken`,
with content SHA256
`446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`.
The tiktoken-go cache filename is the SHA-1 of that canonical URL,
`fb374d419588a4632f3f557e76b4b70aebbca790`; this SHA-1 is only a lookup key,
not an integrity check.

The artifact is acquired at image-build time and independently SHA256-verified,
then atomically installed at
`$TIKTOKEN_CACHE_DIR/fb374d419588a4632f3f557e76b4b70aebbca790`.
Production sets `TIKTOKEN_CACHE_DIR=/opt/integration/tiktoken-cache`. A build-only
`TIKTOKEN_O200K_FETCH_URL` may mirror the bytes, but never changes the canonical
cache key or integrity digest. Runtime startup must not download the tokenizer:
the control-plane adapter fails with `plandex_tokenizer_unavailable` before
launching Plandex when preflight detects a missing or corrupt cache. Both CLI
and server processes inherit the same cache location. Plandex tokenizer source
logic is not modified.

### Pinned tokenizer mirror transport

The default build transport is the immutable raw artifact in
`rmusser01/tldw_chatbook` at commit
`b0dadf19414f5f8faf69b854d8e59007d275083e`, path
`tldw_chatbook/assets/tiktoken_cache/fb374d419588a4632f3f557e76b4b70aebbca790`.
This mirror is not an identity authority: every download must still match the
canonical OpenAI artifact SHA256 and is installed using the cache key derived
from the canonical OpenAI URL. No floating branch or vendored tokenizer is
permitted.

## Plandex local-mode authentication and plan identity

Self-hosted Plandex runs only on `127.0.0.1:8099` with `GOENV=development`,
`LOCAL_MODE=1`, a dedicated PostgreSQL database, and persistent
`PLANDEX_BASE_DIR`. The copied CLI carries a deliberately small upstream patch:
`plandex sign-in --local-host <loopback-url>` enters the existing native local
account/sign-in flow without prompts, while `--validate-only` verifies persisted
native auth through the authenticated org-session API without creating a new
session. Cloud unattended login is not added.

Deployment maps the integration variable `PLANDEX_CLI_HOME` to `HOME` for every
Plandex child. Native `auth.json` and `accounts.json` remain under
`.plandex-home-dev-v2`, mode 0600, with private 0700 directories. Bootstrap runs
once after server/tokenizer readiness; task workers have stdin closed and only
consume validated persisted auth. Tokens never enter task storage, events,
model context, or logs.

The supervisor launches `start-plandex-server.sh`, then runs
`bootstrap-plandex-local.sh` as its readiness gate. The gate waits for `/health`,
preflights the immutable tokenizer cache, validates existing native auth, and
only invokes local sign-in when validation fails. It must succeed before the
control plane accepts Plandex tasks.

`plandex current --json` is the stable machine interface for plan identity. The
control plane stores distinct native plan ID, plan name, project ID, and current
verification state; it does not parse decorated terminal output or overload a
plan name as an ID.

## Phase 4 Graphify repository intelligence

Graphify is a local structural-intelligence dependency, not an orchestrator,
authorization boundary, Git authority, coding agent, or Plandex replacement.
The pinned implementation is `Graphify-Labs/graphify` / `graphifyy==0.9.67`
(tag `v0.9.67`, commit `4c735618f3d56fd622c2049771584621c31ba9ff`).
Only its deterministic `extract --code-only --no-cluster` path is enabled; no
LLM provider, semantic document pass, or external graph database is configured.

The control plane authorizes and prepares a Git worktree, resolves its exact
commit, and then asks the graph adapter to index it. An index identity hashes
the canonical `owner/repo`, exact commit SHA, Graphify version, and integration
schema version. Persistent artifacts live under
`$CONTROL_PLANE_DATA_DIR/graphs/<repository-hash>/<commit>/graphify-<version>-v<schema>/`,
never in an ephemeral worktree. Graphify's native `graphify-out/graph.json`,
manifest, root marker, and cache remain private implementation artifacts; a
control-plane manifest records `NOT_INDEXED`, `INDEXING`, `READY`, `STALE`, or
`FAILED` plus bounded counts and normalized failure category.

A ready base graph represents only the immutable starting commit. Any tracked
or untracked task-worktree change, or a changed `HEAD`, makes its public task
state `STALE`; Phase 4 does not silently refresh or claim that base data is
current. Index generation is idempotent and protected by both process and file
locks, so tasks sharing a repository commit reuse one compatible graph.
Graphify failure is non-destructive and does not fail repository preparation or
Plandex state.

The normalized query boundary initially supports symbol/file matching,
one-hop neighborhoods, dependencies, dependents, and structural summaries by
reading Graphify's native graph JSON. Results expose repository-relative paths,
symbol names, line metadata, and relationship names such as `contains`,
`calls`, `imports`, and `references`; raw process output, internal paths, and
storage-only node identifiers do not enter the API. Graphify receives a minimal
environment without GitHub, Plandex, Spark, Needle, or Open WebUI credentials.
Phase 4B below adds only bounded structural-briefing enrichment; automatic source-file loading remains deferred.

## Phase 4B bounded structural context

Graphify enriches an existing native Plandex plan with a compact structural
briefing; it does not replace Plandex project maps, automatic context, smart
context, or source selection. The insertion point is repository authorization
and exact-SHA indexing, native plan creation, bounded graph enrichment, then
native `plandex load`. The real task objective remains separate and is never
concatenated with graph evidence.

Automatic enrichment requires the persisted graph to be `READY`, its canonical
repository and exact revision to match the task, and the worktree to remain
clean. `STALE`, `FAILED`, missing, dirty, or revision-mismatched graphs emit a
privacy-safe skip and Plandex continues normally. A loaded briefing always says
that its scope is the immutable base revision. Later edits set
`graph_context_current=false`; Phase 4B does not rebuild after each edit.

`GraphContextEnricher` extracts only conservative path/symbol anchors from the
normalized task input, performs at most three normalized graph queries,
deduplicates stable nodes/edges, prefers explicit anchors and `EXTRACTED`
relationships, rejects unsafe paths, and renders deterministic Markdown. The
development limits are 20 nodes, 40 relationships, 10 files, two hops maximum,
12,288 UTF-8 bytes, and a conservative 2,500-token estimate at three bytes per
token. All limits are deployment calibration parameters. No source body,
`graph.json`, full issue payload, absolute path, or credential enters the brief.

The briefing begins with a fixed trust boundary: deterministic structural data
is supporting evidence, not instruction, and source must be inspected before
editing. Relationship direction and Graphify's actual `EXTRACTED`/`INFERRED`
provenance remain visible. Graphify 0.9.67 query logging is explicitly disabled
with its supported `GRAPHIFY_QUERY_LOG_DISABLE=1`; control-plane queries read the
local normalized graph directly and configure no semantic/LLM backend.

Plandex receives the brief through named piped context:
`plandex load --name structural-context-<hash-prefix>`, never `tell` and never a
file in the worktree. The context hash covers repository, revision, Graphify and
adapter versions, and rendered content. Task metadata stores only hash, counts,
budget measurements, load time/status, and current/stale state. The adapter uses
`plandex ls --json` before and after load to verify exactly one native entry and
to make retries idempotent. Named local piped context bypasses Plandex's optional
model-based naming call; smart/auto context remains enabled. Query, rendering,
or load failure is non-critical and cannot fail the coding task.

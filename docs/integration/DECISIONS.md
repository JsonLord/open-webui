# Architecture decisions

## ADR-001 — Remote Spark is the baseline coding model

**Context:** The historical specification required local GGUF models through
llama.cpp and llama-swap. The current specification explicitly replaces that
baseline.

**Decision:** Spark is remote and configured only through `SPARK_BASE_URL`,
`SPARK_MODEL`, and `SPARK_API_KEY`. Local Spark and MiniCPM are not startup
requirements.

**Reason:** This preserves CPU capacity for the application and the future
Needle3 model while matching the deployment architecture.

**Alternatives:** Local llama.cpp; llama-swap; direct Plandex-to-Spark calls.

**Consequences:** Spark availability is external. A deployer must supply the
verified model ID and credentials.

**Files affected:** `.env.example`, `scripts/integration/`, `integration/`.

## ADR-002 — Headroom is the mandatory Spark gateway

**Context:** Coding context must be compressed before remote inference.

**Decision:** Plandex's provider URL is fixed to
`http://127.0.0.1:8787/v1`. Headroom forwards compressed requests to the
loopback Spark contract; only that contract receives `SPARK_BASE_URL`.

**Reason:** It makes bypassing compression a detectable configuration error and
keeps upstream details outside the Plandex model pack.

**Alternatives:** Direct calls; production use of `headroom wrap`.

**Consequences:** Headroom readiness becomes a prerequisite for coding jobs.

**Files affected:** `integration/plandex-models.template.json`,
`scripts/integration/render-plandex-models.py`, `start-headroom.sh`.

## ADR-003 — Accept SPARK_API_TOKEN only as an input alias

**Context:** The test deployment supplies `SPARK_API_TOKEN`, while the stable
configuration contract names `SPARK_API_KEY`.

**Decision:** Startup and smoke tooling accepts either name, preferring
`SPARK_API_KEY`, and exports the canonical name internally.

**Reason:** This supports the current test without permanently bifurcating the
Plandex provider configuration.

**Alternatives:** Rename the permanent contract; commit a placeholder token.

**Consequences:** Deployment secrets remain external and no credential is
written to generated configuration.

**Files affected:** `.env.example`, `scripts/integration/common.sh`,
`scripts/integration/spark-smoke.py`.

## ADR-004 — Validate the final post-compression Spark request

**Context:** Headroom compression changes request size. Validating before it
would not enforce the actual request received by remote Spark, whose physical
context ceiling is 32,768 tokens.

**Decision:** A small standard-library loopback adapter on port 8790 sits after
Headroom. It accepts only the supported OpenAI chat fields, normalizes the model
to `spark-x2.5-1.7b`, enforces 24,576 input + 4,096 output + 4,096 reserve,
applies configurable defaults/timeouts, and performs at most one default retry
for connection failures or HTTP 502/503/504 before a response begins.

**Reason:** This prevents arbitrary provider parameters and over-budget prompts
from reaching Spark without taking compression responsibility away from
Headroom.

**Alternatives:** Validation before Headroom; direct forwarding; silently
truncating prompts; adding a heavyweight gateway.

**Consequences:** Headroom targets the adapter rather than the Space. When no
exact local tokenizer is available, a conservative UTF-8 bytes/3 plus framing
estimate is used and reported as an estimate. Oversized prompts fail with a
structured 413 and must be reduced upstream.

The 32,768 physical ceiling is not an operational tuning knob: deployments may
lower it but cannot configure a larger value. Contract-to-Spark integration
tests use a local fake upstream so transport, model discovery, sanitization, and
stream preservation are verified without confusing that with live model
verification.

**Files affected:** `integration/spark_contract.py`,
`scripts/integration/spark-contract-server.py`, `start-headroom.sh`, and Spark
environment documentation.

## ADR-005 — Needle3 selects but never executes tools

**Context:** Upstream Needle offers `run()`, which invokes registered Python
functions. The platform requires model output to cross independent schema and
policy gates before deterministic execution.

**Decision:** The ToolRouter uses Needle3 `complete()` only, with at most five
task-derived candidates. It validates the selected name and arguments with JSON
Schema, then enforces confidence, role, effect, and confirmation policy. A
separate ToolGateway invokes only pre-registered handlers for `selected`
decisions. Default allowed effects are read-only.

**Reason:** Model inference cannot directly exercise capabilities, and a small
candidate surface improves both safety and routing quality.

**Alternatives:** Upstream `run()`; expose the global inventory; use Spark for
tool selection; merge selection and execution into a control-plane loop.

**Consequences:** Multi-call output must be decomposed or confirmed. Missing or
low confidence abstains, suppressed calls require confirmation, and unknown or
schema-invalid calls fail closed. Candidate selection and actual handlers remain
the responsibility of deterministic application code.

**Files affected:** `integration/tool_router.py`, `test/test_tool_router.py`,
`.env.example`, and `integration/requirements.txt`.

## ADR-006 — Bake stock Needle3 artifacts and preserve the native base path

**Context:** In `cactus-needle==3.0.1`, omitting `weights=` uses process-global
native-library/base-weight registries. Passing an explicit `.cact` marks it as
tuned, starts `FineTuneWorker`, and suppresses calibrated confidence. Runtime
downloads are unacceptable in the HF Space.

**Decision:** The baseline omits `weights=`, bakes the generation-3 engine and
`needle3.cact` into the package's exact cache layout during image build, verifies
immutable revision/checksums, and starts only after offline preflight.
`NEEDLE_CUSTOM_WEIGHTS` exists solely for future tuned archives. Each stock
route creates a fresh schema session for independence; the process-global heavy
artifacts remain loaded. Routes are serialized around the native global state.

**Reason:** This retains calibrated base-model confidence, avoids a worker per
route, prevents unrelated history accumulation, and eliminates request-time
network access without monkey-patching upstream.

**Alternatives:** Set the base archive through `weights=`; runtime downloads;
patch upstream paths; add an unmeasured schema-agent cache.

**Consequences:** Builds fail until the immutable revision and both SHA256 pins
are supplied. Real offline inference and performance numbers remain unverified
while Hugging Face is blocked. A benchmark will determine whether schema-session
caching is justified; none is added speculatively.

**Files affected:** `integration/needle_runtime.py`, `integration/Dockerfile.needle`,
`scripts/integration/fetch-needle-artifacts.py`, preflight/benchmark/evaluation
scripts, `.env.example`, and `third_party/versions.lock`.

## ADR-007 — Control plane is an authenticated Open WebUI backend module

**Context:** Open WebUI already provides the public FastAPI process and authentication, while Plandex has a separate execution server. Adding a third public web stack would duplicate deployment and security concerns.

**Decision:** The control plane lives under `backend/open_webui/control_plane/` and exposes an authenticated router at `/api/v1/control-plane`. Domain, storage, Git, GitHub MCP, and Plandex adapters remain isolated from the HTTP layer.

**Reason:** The UI gets one stable same-origin API while Plandex remains the coding engine rather than becoming the lifecycle database.

**Consequences:** Open WebUI startup owns the control-plane schema initialization. No ToolGateway or raw MCP method is exposed over HTTP.

## ADR-008 — Persist task lifecycle in a dedicated PostgreSQL schema

**Context:** Both Open WebUI and Plandex already use database infrastructure, and task/event history must survive process restarts.

**Decision:** Production uses `control_plane.tasks` and `control_plane.task_events` in PostgreSQL. Payloads exclude secrets and prompt/model transcripts. The in-memory store exists only as a test double.

**Reason:** This avoids another production database while maintaining a clear schema boundary and an append-only ordered event surface.

**Consequences:** PostgreSQL is required for production control-plane startup; storage is designed for a later scheduler but Phase 3 permits one active coding task.

## ADR-009 — Git owns local state; official GitHub MCP owns GitHub context

**Context:** Worktrees and diffs are local deterministic operations, while issues, PRs, and checks are GitHub API objects.

**Decision:** The pinned official `github/github-mcp-server` runs as a read-only stdio child with only `repos,issues,pull_requests,actions`. Ordinary `git` performs clone, fetch, branches, worktrees, diff, commit, and push. `GITHUB_PAT` is canonical and subprocess aliases are generated without persistence or logging.

**Reason:** This minimizes MCP capabilities and keeps local repository mutation out of model-selected remote tools.

**Consequences:** Write-side PR operations remain a later explicitly authorized capability. Phase 3's MCP runtime is not verified without a PAT.

## ADR-010 — Every coding task uses an allowlisted isolated worktree

**Context:** Concurrent or failed agent work must not edit the canonical checkout and user input must not select arbitrary hosts or paths.

**Decision:** Normalize and authorize `owner/repo` before cloning. Cached bare repositories live below a fixed root and each task receives `agent/<slug>-<short-id>` in a contained task worktree. Worktrees are preserved on cancellation for diagnosis.

**Reason:** Deterministic argv construction, containment, and isolation reduce credential, traversal, and cross-task risks.

**Consequences:** Cleanup is explicit rather than automatic, and idempotent retries reuse only a worktree registered to the same task.

## ADR-011 — Interrupted tasks fail closed on control-plane restart

**Context:** Phase 3 has no distributed execution checkpoint/resume engine. Re-running a partially completed Git or Plandex operation after process restart could duplicate effects or silently report success.

**Decision:** On production store initialization, every non-terminal task transitions atomically to `FAILED` with `last_error=recovery_required` and an ordered recovery event. Terminal tasks remain unchanged. Worktrees remain available for diagnosis.

**Reason:** Explicit operator-visible failure is safer than guessing whether an owned child process or external operation completed.

**Alternatives:** Automatic resumption, leaving tasks indefinitely non-terminal, or a new scheduler/checkpoint subsystem.

**Consequences:** Operators may inspect and deliberately retry work; later checkpoint-aware scheduling can replace this conservative policy.

## ADR-012 — GitHub MCP builds with its exact upstream Go directive

**Context:** Pinned GitHub MCP v1.12.2 declares Go 1.25.12. Building it with the ambient Go version triggered automatic toolchain drift.

**Decision:** The build stage pins `golang:1.25.12-bookworm`, checks out commit `85598ba6e1256f7ebf4867b95d63b833c4549264`, embeds version/commit metadata, and copies only the static binary to `/opt/integration/bin/github-mcp-server`.

**Reason:** The source revision, compiler generation, reported version, and runtime contents must be reproducible and auditable.

**Consequences:** The final application image does not contain Go. Updating MCP requires updating both the immutable source pin and Go build version.

## ADR-013 — Plandex tokenizer assets are verified and baked

**Context:** `tiktoken-go v0.1.7` downloads `o200k_base.tiktoken` during shared
Plandex initialization when its URL-keyed cache is empty. Runtime network access
is unavailable and the SHA-1 cache filename does not establish content integrity.

**Decision:** Fetch the canonical bytes (or a build-only mirror) during image
build, verify canonical SHA256, and install atomically under the SHA-1 of the
canonical URL in `/opt/integration/tiktoken-cache`. Preflight verifies the
artifact before any Plandex child starts.

**Reason:** Plandex initialization becomes network-independent and corrupt or
substituted cache content fails closed without changing Plandex tokenizer code.

**Consequences:** A build cannot finish without the exact verified bytes. The
runtime cache is read-only. Mirror identity never affects runtime cache identity.

## ADR-014 — A pinned GitHub mirror transports the canonical tokenizer

**Context:** The canonical Azure Blob endpoint is inaccessible, while immutable
GitHub raw content is reachable in the deployment environment.

**Decision:** Use `rmusser01/tldw_chatbook` commit
`b0dadf19414f5f8faf69b854d8e59007d275083e` as the default build transport.
Continue to derive the runtime cache key from the canonical OpenAI URL and
accept bytes only when their SHA256 equals the canonical digest.

**Reason:** Transport availability must not redefine artifact identity or weaken
integrity validation.

**Consequences:** No tokenizer blob is vendored. Changing mirrors requires an
immutable revision and does not permit changing the canonical digest.

## ADR-015 — Provision native local Plandex auth once in a dedicated CLI home

**Context:** A pristine CLI invokes interactive auth, which is unsafe and
impossible inside a non-interactive task worker.

**Decision:** Extend the copied Plandex CLI with local-only
`sign-in --local-host`, reuse the native server-authoritative account/session/org
flows, and validate existing auth through `--validate-only`. Deployment persists
a private `PLANDEX_CLI_HOME`; workers inherit it with stdin closed.

**Reason:** No control-plane code fabricates tokens, auth JSON, users, or orgs,
and repeated startup neither prompts nor duplicates native records.

**Consequences:** The small CLI extension must be rebased when updating copied
Plandex. Auth files are 0600, directories are 0700, and health exposes only
booleans. `current --json` supplies stable native identity without terminal
scraping.

## ADR-016 — Graphify indexes immutable repository revisions behind a normalized adapter

**Context:** Structural repository intelligence must be reusable across tasks
without turning an ephemeral worktree, branch name, or Graphify storage ID into
a source of truth.

**Decision:** Pin Graphify 0.9.67 and run only local `--code-only --no-cluster`
extraction. Key each persistent index by canonical repository, exact Git SHA,
Graphify version, and adapter schema. The control plane owns authorization and
source paths; a narrow adapter owns generation, manifests, normalized queries,
and conservative dirty-worktree stale detection.

**Reason:** Immutable base indexes can be shared and recovered after restart,
while Graphify remains isolated from credentials and agent/model execution.

**Alternatives:** One index per task, mutable branch-keyed indexes, Neo4j, direct
Graphify CLI output in APIs, or immediate prompt injection.

**Consequences:** A dirty worktree is explicitly `STALE`; no task-specific
incremental refresh is claimed yet. Index failure is optional supporting
infrastructure and cannot corrupt Git or Plandex state. Context enrichment is a
separate future increment.

## ADR-017 — Graphify enriches Plandex through bounded native context

**Context:** Graph structure can orient planning, but whole graphs, automatic
source loading, or prompt concatenation would duplicate Plandex smart context
and blur evidence with instruction.

**Decision:** Build a normalized, exact-revision `GraphContext`, render a strict
budgeted evidence briefing, and load it as a deterministically named native
Plandex piped-context entry. Verify with `plandex ls --json`; persist only hash,
counts, status, and timings. Keep smart and automatic context enabled.

**Reason:** Structural hints become reusable plan context without turning
Graphify into a planner, expanding user instructions, writing task files, or
requiring inference. Hash and native-name checks provide idempotency.

**Alternatives:** `plandex tell`, loading every ranked source file, committing a
Markdown artifact, Graphify MCP, whole `graph.json`, or replacing smart context.

**Consequences:** Enrichment is skipped for non-ready, mismatched, stale, dirty,
or zero-match graphs and any query/load failure remains non-critical. Loaded
context describes only the base SHA; post-edit refresh is deliberately deferred.
The copied Plandex CLI adds `load --name` and `ls --json`, which must be rebased
with the existing local-mode patch during an upstream update.

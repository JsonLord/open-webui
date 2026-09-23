# spec.md — Transform Plandex into a GitHub-First JIT-Planned Local Coding Worker

**Base repository:** `plandex-ai/plandex`  
**Deployment target:** Hugging Face Docker Space, CPU-only  
**Primary coding model:** Spark-X2.5-1.7B Q4 via `llama.cpp`, routed through `llama-swap`  
**Meta-planner:** JIT-Agent driven by a large-context cloud model  
**Repository intelligence:** Plandex project maps first, Graphify for deeper graph reasoning  
**GitHub integration:** official GitHub MCP Server + `git`/`gh`  
**Idle review:** Alibaba Open Code Review + MiniCPM5-2B Q4 via the same `llama-swap` + `llama.cpp` backend  
**Priority rule:** interactive coding always preempts review/maintenance

## 1. Goal

Do not rebuild a coding agent from scratch. Fork Plandex and reuse its existing:

- planner / architect / coder role separation;
- custom model packs and OpenAI-compatible providers;
- automatic context loading and Tree-sitter project maps;
- smart per-step context;
- version-controlled pending-change sandbox;
- builder and whole-file-builder edit paths;
- command execution, automated debugging, retries, and rollback;
- internal plan history / branches;
- Git integration and optional commits;
- self-hosted server.

Add only what Plandex lacks:

1. GitHub-first task/branch/PR lifecycle.
2. Official GitHub MCP integration.
3. JIT-Agent as cloud meta-planner / execution-profile generator.
4. Spark-X2.5-1.7B as local coding worker.
5. CPU resource arbitration and local-model swapping.
6. Graphify as optional code-graph escalation.
7. Idle-only PR review with Open Code Review + MiniCPM.
8. Automatic review -> repair -> re-review.
9. HF single-container deployment.
10. Optional Headroom benchmarking later.

The transformed system must remain recognizably Plandex internally.


## 2. Target architecture

```text
                              USER / API
                                  |
                                  v
                       HF Control Plane :7860
                                  |
                       Priority / Resource Arbiter
                                  |
          +-----------------------+-----------------------+
          |                                               |
          | PRIMARY CODING                                | IDLE
          v                                               v
   GitHub task / issue                             Open PR queue
          |                                               |
          v                                               v
   GitHub MCP context                             Open Code Review
          |                                               |
          v                                               v
   clone/fetch branch                              MiniCPM5-2B Q4
          |                                               |
          v                                               v
   Plandex project map                           structured findings
          |
          +------> optional Graphify
          |
          v
      JIT meta-plan
   large cloud model
          |
          v
  PlandexExecutionProfile
          |
          v
       PLANDEX
 +---------------------+
 | context selection   |
 | planner/coder roles |
 | sandbox/builders    |
 | tests/debug/retry   |
 +---------------------+
          |
          v
 Spark-X2.5-1.7B Q4
 via llama.cpp
          |
          v
   validated pending diff
          |
          v
     apply / commit
          |
          v
       push + PR
          |
          +-----------------> idle review queue
```

### Reuse-before-adding rule

Prefer native Plandex facilities over equivalent new dependencies.

- Plandex project maps remain the default repository map.
- Plandex smart context remains the default context compressor.
- Plandex pending changes remain the edit sandbox.
- Plandex auto-debug remains the execution repair loop.
- Graphify adds call/dependency/impact graph reasoning.
- Serena is optional only if Plandex + Graphify prove insufficient.
- Headroom is optional only after A/B benchmarking.


## 3. New control-plane layer

Add a thin orchestration layer around the existing Plandex server.

Suggested structure:

```text
app/controlplane/
  api/
  scheduler/
  jobs/
  github/
  jit/
  models/
  graphify/
  review/
  maintenance/
  security/
```

The control plane owns:

- GitHub task intake;
- repo/org allowlisting;
- Git branch/worktree lifecycle;
- GitHub MCP subprocess;
- priority queue and cancellation;
- JIT invocation;
- Graphify invocation;
- local model lifecycle;
- PR creation;
- idle review queue;
- review-to-repair scheduling;
- HF-facing status API.

Plandex continues to own:

- code context;
- planning/execution semantics;
- file editing/builders;
- pending diff sandbox;
- command execution;
- test/debug retry behavior;
- plan history.


## 4. GitHub-first task lifecycle

For each primary task:

```text
1. validate owner/repo against allowlist
2. read task/issue metadata through GitHub MCP
3. clone or fetch repo
4. create clean task branch/worktree
5. create/open Plandex plan in that worktree
6. build/update Plandex project map
7. optionally build/update Graphify graph
8. obtain JIT execution profile
9. execute through native Plandex
10. validate pending changes
11. apply
12. run final tests
13. commit
14. push
15. create/update PR
16. queue PR for idle review
```

Branch format:

```text
agent/<task-slug>-<short-id>
```

Never modify the default branch directly.

### GitHub authentication

Canonical secret:

```text
GITHUB_PAT
```

Derive only for subprocesses:

```text
GitHub MCP:
GITHUB_PERSONAL_ACCESS_TOKEN=<GITHUB_PAT>

gh:
GH_TOKEN=<GITHUB_PAT>
```

Never expose the PAT to repo test/build commands, cloud prompts, logs, status APIs, or PR text.


## 5. Official GitHub MCP

Use:

```text
github/github-mcp-server
```

Run it as a pinned local stdio subprocess. Do **not** merge its source tree into Plandex.

Initial toolsets should be narrow:

```text
repos
issues
pull_requests
actions
```

Use GitHub MCP for:

- repo metadata/access checks;
- issue content;
- PR metadata;
- checks/actions status;
- review threads;
- PR creation/registration where appropriate.

Use ordinary `git` for:

- clone/fetch;
- worktrees;
- branches;
- diff;
- add;
- commit;
- push.

Keep `gh` installed for deterministic GitHub operations where simpler than MCP.

Do not expose the whole GitHub MCP surface to Spark.


## 6. Local model routing

### Spark execution

Use:

```text
XHToken/Spark-X2.5
Spark-X2.5-1.7B Q4
```

served by:

```text
ggml-org/llama.cpp
```

Internal endpoint:

```text
http://127.0.0.1:8080/v1
```

Create a Plandex custom OpenAI-compatible provider and a model pack such as:

```text
spark-worker
```

Initial pack:

```text
architect          -> cloud-capable model
planner            -> cloud-capable model
coder              -> Spark-X2.5-1.7B
builder            -> Spark-X2.5-1.7B
wholeFileBuilder   -> Spark-X2.5-1.7B
summarizer         -> Spark if reliable, otherwise cheap fallback
autoContinue       -> Spark
commitMessages     -> Spark
```

Initial Spark context target:

```text
8K-16K input
2K-4K output
```

Do not use the architectural maximum context merely because the model supports it.



## 6A. llama-swap + llama.cpp model runtime

Use `mostlygeek/llama-swap` as the **single stable local inference gateway** and `ggml-org/llama.cpp` as the actual inference backend for both local models.

The application should no longer directly start/stop `llama-server` for ordinary model switching.

Stable internal endpoint:

```text
http://127.0.0.1:8080/v1
```

`llama-swap` listens on this endpoint and starts/stops individual `llama-server` processes on demand according to the OpenAI `model` field.

Required model IDs:

```text
spark-worker
minicpm-review
```

Suggested baseline configuration:

```yaml
models:
  spark-worker:
    cmd: >
      llama-server
      --port ${PORT}
      --model /models/spark-x2.5-1.7b-q4.gguf
      --ctx-size 8192
      --jinja
    ttl: 300

  minicpm-review:
    cmd: >
      llama-server
      --port ${PORT}
      --model /models/minicpm5-2b-q4_k_m.gguf
      --ctx-size 8192
      --jinja
    ttl: 300

routing:
  router:
    use: group
    settings:
      groups:
        local-llms:
          swap: true
          exclusive: true
          members:
            - spark-worker
            - minicpm-review
```

The two models belong to one exclusive swap group:

```text
only one local model runs at a time
```

Do not use `matrix` concurrency in v1.

`llama-swap` owns:

- starting the required `llama-server`;
- assigning ephemeral upstream ports;
- readiness/health waiting;
- graceful unload;
- idle TTL unload;
- stable OpenAI-compatible routing;
- local inference logs;
- reporting which model is currently running.

The application scheduler owns:

- job priority;
- idle-review eligibility;
- cancellation/preemption;
- whether the next inference request should target `spark-worker` or `minicpm-review`.

Separation of responsibility:

```text
Scheduler decides WHAT may run.
llama-swap decides WHICH model process must be active.
```

Primary coding:

```text
Plandex
  -> baseURL http://127.0.0.1:8080/v1
  -> model spark-worker
  -> llama-swap
  -> Spark llama-server
```

Idle review:

```text
Open Code Review
  -> baseURL http://127.0.0.1:8080/v1
  -> model minicpm-review
  -> llama-swap
  -> MiniCPM llama-server
```

If a primary coding task arrives during review:

```text
1. preempt/cancel OCR at a safe boundary;
2. send the next request using `model=spark-worker`;
3. llama-swap unloads MiniCPM;
4. llama-swap starts Spark;
5. primary coding resumes.
```

Useful internal observability endpoints include:

```text
/v1/models
/running
/logs
/logs/stream
```

Use these for health/status only. Do not create a second scheduling policy around them.

Pin the `llama-swap` release/commit in `third_party/versions.lock`.


## 7. JIT-Agent integration

Upstream:

```text
bingreeky/JIT
```

JIT is the **only external project that should be selectively vendored into the Plandex fork**, because its behavior must be adapted rather than merely invoked unchanged.

Do not ship JIT-27B. Use a hosted large-context OpenAI-compatible model as the JIT meta-model.

### Change JIT's output contract

Do not execute arbitrary generated Python harnesses inside the coding worker.

Adapt JIT to emit a validated typed object:

```text
PlandexExecutionProfile
```

Example:

```json
{
  "task_type": "bug_fix",
  "autonomy": "full",
  "context": {
    "auto_load": true,
    "smart_context": true,
    "graphify": true,
    "graphify_queries": []
  },
  "planning": {
    "seed_plan": [],
    "max_steps": 12
  },
  "execution": {
    "auto_exec": true,
    "auto_debug": true,
    "max_debug_tries": 3
  },
  "validation": {
    "commands": [],
    "require_tests": true,
    "require_graph_impact": true
  },
  "github": {
    "include_issue": true,
    "include_pr_context": false
  },
  "budgets": {
    "wall_seconds": 1200
  }
}
```

JIT chooses:

- task class;
- Plandex autonomy;
- context policy;
- whether Graphify is needed;
- high-level plan seed;
- debug/retry budget;
- validation commands from an approved set;
- GitHub context requirements;
- stopping/budget conditions.

JIT never directly edits files.

### Avoid duplicate cloud planning

First milestone may retain Plandex's native cloud planner.

After JIT works, evaluate letting JIT provide the plan seed/profile so Plandex does not make a second expensive cloud planning call unnecessarily.


## 8. Graphify

Upstream:

```text
Graphify-Labs/graphify
```

Do **not** merge Graphify source.

Install the pinned official package:

```text
graphifyy
```

Plandex project maps remain the default context mechanism.

Use Graphify only for:

- callers/callees;
- cross-file dependencies;
- call chains;
- inheritance;
- architectural relationships;
- blast radius;
- pre-PR impact analysis.

Flow:

```text
Plandex project map
       |
       +--> routine task -> continue
       |
       +--> complex structural task
                    |
                    v
                Graphify
                    |
                    v
          concise graph result
                    |
                    v
           inject into Plandex
```

Graph artifacts belong in runtime cache, never the user's Git branch.


## 9. Optional tools

### Serena

Upstream:

```text
oraios/serena
```

Do **not** vendor current Serena application source into this MIT Plandex fork. Current Serena application code is GPL-3.0-or-later.

If testing later proves Serena materially improves Spark:

- run Serena as a separate MCP subprocess;
- keep the process/code boundary explicit;
- or evaluate only its separately MIT-licensed SolidLSP component.

Serena is not a baseline requirement.

### Context7

Do not merge. Use only as optional remote MCP for dependency upgrades and version-specific APIs.

### Headroom

Upstream:

```text
headroomlabs-ai/headroom
```

Do not merge. Install only for A/B benchmarking.

Compare:

```text
A: Plandex -> Spark
B: Plandex -> Headroom -> Spark
```

Measure task success, prompt size, prefill time, wall time, and lost-context failures before deciding to keep it.


## 10. Idle PR review

Use:

```text
alibaba/open-code-review
```

Do not merge source. Install a pinned CLI/package:

```text
@alibaba-group/open-code-review
```

Run:

```text
ocr review --format json --output <result>
```

Review only while no primary coding task is active.

Review model:

```text
OpenBMB/MiniCPM
MiniCPM5-2B Q4_K_M
```

served through the same llama.cpp model slot.

MiniCPM reviews; it does not edit.

If review finds issues:

```text
OCR findings
 -> unload MiniCPM
 -> load Spark
 -> create Plandex repair plan
 -> repair same PR branch
 -> tests
 -> commit/push
 -> queue re-review
```

Maximum automatic repair cycles:

```text
2
```

Repeated findings or repeated regressions become:

```text
needs-human-review
```


## 11. Resource arbiter

Use one active local inference model.

```text
CODING:
  Spark loaded

REVIEW:
  MiniCPM loaded

IDLE_FIX:
  Spark loaded

IDLE:
  no model or Spark kept warm if beneficial
```

Priority:

```text
P0 user coding
P1 requested repair/follow-up
P2 idle PR review
P3 idle automatic repair
P4 indexing/housekeeping
```

Suggested idle grace:

```text
180 seconds
```

A P0 task preempts maintenance. Do not run Spark and MiniCPM concurrently in v1.


## 12. Repository integration matrix

| Repository | Role | Merge into Plandex? | Integration |
|---|---|---:|---|
| `plandex-ai/plandex` | Base execution engine | **YES — fork/base** | Fork; preserve upstream remote |
| `bingreeky/JIT` | Cloud meta-planner | **SELECTIVE VENDOR** | Vendor only runtime/meta-harness code needed for adaptation |
| `github/github-mcp-server` | GitHub MCP | **NO** | Build/install pinned binary; stdio |
| `Graphify-Labs/graphify` | Code knowledge graph | **NO** | Install pinned `graphifyy` |
| `alibaba/open-code-review` | Idle PR review | **NO** | Install pinned npm/release CLI |
| `OpenBMB/MiniCPM` | Review model | **NO** | Download only selected model/GGUF |
| `XHToken/Spark-X2.5` | Coding model | **NO** | Download only selected model/GGUF |
| `mostlygeek/llama-swap` | Local model router/swapper | **NO** | Install/build pinned binary; stable OpenAI endpoint |
| `ggml-org/llama.cpp` | Local inference backend | **NO** | Build pinned `llama-server` binary in Docker |
| `headroomlabs-ai/headroom` | Optional compression | **NO** | Optional pinned runtime dependency |
| `oraios/serena` | Optional LSP MCP | **NO** | Separate process only if enabled |
| Context7 | External docs | **NO** | Remote MCP only |

The key point:

> Do not flatten all upstream projects into one monorepo. Plandex is the fork; JIT is the only selectively vendored source integration.


## 13. Licensing / notices

Create:

```text
THIRD_PARTY_NOTICES.md
third_party/README.md
third_party/versions.lock
```

Known repo licenses at the time of this spec:

- Plandex: MIT
- JIT code: MIT
- GitHub MCP Server: MIT
- llama-swap: MIT
- Graphify: Apache-2.0
- Open Code Review: Apache-2.0
- Headroom: Apache-2.0
- MiniCPM repository code: Apache-2.0
- Spark-X2.5 repository code: Apache-2.0
- Serena application: GPL-3.0-or-later
- Serena SolidLSP component: MIT

For vendored JIT:

- preserve upstream MIT license/copyright;
- record exact commit SHA;
- record copied paths;
- do not vendor benchmark datasets;
- do not vendor unrelated third-party data.

For Apache dependencies, keep required notices when redistributing.

This is engineering guidance, not legal advice.


## 14. Create the fork

```bash
git clone https://github.com/<YOUR_ACCOUNT>/plandex.git
cd plandex

git remote rename origin fork
git remote add upstream https://github.com/plandex-ai/plandex.git

git fetch upstream
git switch -c transform/hf-jit-worker upstream/main
```

Keep:

```text
fork      -> transformed repo
upstream  -> plandex-ai/plandex
```

Concentrate custom code under:

```text
app/controlplane/
third_party/jit/
scripts/hf/
docs/transformation/
```

Minimize invasive edits to existing Plandex packages so upstream merges remain feasible.


## 15. Vendor JIT selectively

Do **not** `git merge` the entire JIT repository.

Upstream JIT contains benchmark/evaluation/data content that does not belong in production runtime.

Create:

```text
third_party/jit/
scripts/vendor_jit.sh
```

Audit these upstream areas:

```text
jit/
harness_factory/
scripts/
requirements.txt / relevant dependency metadata
```

Exclude by default:

```text
benchmark/
dataset/
assets/
local JIT-27B serving artifacts
evaluation-only scripts
```

`vendor_jit.sh` must:

1. accept a pinned JIT commit;
2. clone to a temp directory;
3. sparse-checkout approved runtime paths;
4. copy them into `third_party/jit/`;
5. preserve JIT's LICENSE;
6. write the SHA into `third_party/versions.lock`;
7. remove the temp clone.

Do not hand-copy untracked upstream files.


## 16. Runtime dependencies are installed, not merged

For these projects, installation is the integration:

```text
github/github-mcp-server
Graphify-Labs/graphify
alibaba/open-code-review
mostlygeek/llama-swap
ggml-org/llama.cpp
headroomlabs-ai/headroom
```

Pin all versions/commits in:

```text
third_party/versions.lock
```

Suggested shape:

```yaml
plandex_upstream: <sha>
jit: <sha>
github_mcp_server: <tag-or-sha>
graphifyy: <version>
open_code_review: <version>
llama_swap: <tag-or-sha>
llama_cpp: <sha>
headroom: <version-or-disabled>
spark_model: <model-id/revision>
minicpm_model: <model-id/revision>
```

Never build production images from unpinned `main`.


## 17. HF Docker topology

One Hugging Face Docker Space.

Public:

```text
:7860 control-plane API
```

Internal:

```text
:8099 existing Plandex server or current upstream port
:8080 llama-swap stable OpenAI-compatible gateway
ephemeral localhost ports: llama.cpp model servers managed by llama-swap
GitHub MCP via stdio
Graphify via CLI/subprocess
OCR via CLI
JIT adapter via Python module/subprocess
```

Plandex's standard self-hosted setup uses PostgreSQL.

For the first transformation:

- preserve Plandex DB semantics;
- run PostgreSQL inside the same container under supervision if required, or use an external development DB while bringing up the system;
- do not immediately rewrite persistence to SQLite;
- measure memory before simplifying persistence.


## 18. API and state

HF-facing endpoints:

```text
GET  /health
GET  /status
POST /tasks
GET  /tasks/{id}
POST /tasks/{id}/cancel
GET  /maintenance/status
POST /maintenance/run
POST /maintenance/pause
```

Task example:

```json
{
  "repo": "owner/repo",
  "base": "main",
  "task": "Fix issue #52",
  "issue": 52,
  "mode": "pr"
}
```

Modes:

```text
workspace
push
pr
```

Job states:

```text
QUEUED
PREPARING_REPO
PLANNING_CONTEXT
JIT_PLANNING
PLANDEX_EXECUTING
VALIDATING
APPLYING
COMMITTING
PUSHING
CREATING_PR
COMPLETED

IDLE_WAIT
REVIEWING
REVIEW_FINDINGS
REPAIR_QUEUED
REPAIRING
REREVIEW_PENDING

PREEMPTING
CANCELLED
FAILED
```


## 19. Security boundary

Initial deployment supports only explicitly allowed repos/orgs.

```text
ALLOWED_REPOS=
ALLOWED_GITHUB_ORGS=
GITHUB_PAT=
META_API_BASE=
META_API_KEY=
META_MODEL=
```

Repository code is untrusted.

Never pass service secrets into arbitrary repository scripts.

Timeout all subprocess/tool execution.

Run as non-root where practical.

A later phase may add SWE-ReX or another execution sandbox.


## 20. Transformation phases

### Phase 0 — Plandex audit

Before patching:

- map server startup and DB dependencies;
- map model-role routing;
- map project-map generation;
- map programmatic plan creation;
- map pending-change apply path;
- map auto-debug;
- map Git integration;
- find non-REPL APIs suitable for the control plane.

Deliver:

```text
docs/transformation-audit.md
```

### Phase 1 — HF Plandex + local Spark

Implement:

- HF launcher/supervisor;
- Plandex server;
- persistence needed by current Plandex;
- llama.cpp;
- llama-swap;
- two-model llama-swap configuration for Spark + MiniCPM;
- Spark-X2.5-1.7B Q4;
- Plandex local custom provider pointing to llama-swap;
- `spark-worker` model pack;
- health/status.

Acceptance:

```text
repo -> Plandex project map -> cloud plan -> Spark coder/builder
     -> pending diff -> Plandex test/debug -> valid change
```

No OpenCode dependency.

### Phase 2 — GitHub-first control plane

Implement:

- GITHUB_PAT;
- GitHub MCP;
- allowlists;
- clone/fetch/worktrees;
- issues/PRs;
- commit/push/PR.

Acceptance:

```text
GitHub issue -> branch -> Plandex -> commit -> push -> PR
```

### Phase 3 — JIT meta-planner

Vendor only required JIT code.

Implement `PlandexExecutionProfile`.

Acceptance:

- cloud meta-model returns validated profile;
- profile configures Plandex;
- Graphify can be enabled/disabled;
- arbitrary generated commands/capabilities are rejected.

### Phase 4 — Graphify escalation

Install `graphifyy`.

Add graph build/query/path/impact adapters and context injection.

### Phase 5 — Resource arbiter

Implement P0-P4, idle grace, maintenance preemption, and llama-swap model selection. Do not reimplement model-process swapping in the scheduler.

### Phase 6 — Idle OCR review

Install OCR, load MiniCPM only while idle, produce JSON findings.

### Phase 7 — Automatic repair

Convert findings to a Plandex repair task on the same PR branch. Max 2 cycles.

### Phase 8 — Benchmarked extras

Evaluate only after baseline works:

- Headroom;
- Serena;
- Context7;
- persistence simplification;
- alternate quantizations.


## 21. First Codex target

Codex should start with:

```text
Phase 0 + Phase 1
```

Do not merge every dependency first.

First milestone:

1. audit current Plandex;
2. identify the programmatic plan/execution path;
3. create HF-compatible single-container startup;
4. run Plandex successfully;
5. run `llama-swap` as the stable local OpenAI-compatible endpoint;
6. configure llama-swap to manage Spark and MiniCPM through `llama-server`;
7. verify `model=spark-worker` loads Spark on demand;
8. register the llama-swap endpoint as the Plandex custom provider;
9. create `spark-worker` pack;
10. route coder/builder to Spark;
11. execute one small real code change through native Plandex;
12. preserve upstream tests/behavior.

Only after that add GitHub MCP, JIT, Graphify, and idle review.


## 22. Definition of success

The transformed project is successful when:

- Plandex remains the execution engine.
- JIT/cloud model selects task execution strategy.
- Spark performs local coding/building.
- Plandex maps/smart context handle normal repository navigation.
- Graphify adds deeper graph reasoning when needed.
- GitHub MCP handles GitHub-side task/PR context.
- Git/gh handle deterministic repository lifecycle.
- OCR independently reviews PRs during idle time.
- MiniCPM powers review only when primary coding is inactive.
- Spark repairs review findings on the same PR branch.
- primary user jobs always preempt maintenance.
- no duplicate generic coding-agent runtime is added without evidence.
- the system remains viable on a constrained HF CPU Space.


## 23. Upstream maintenance

Keep the fork updateable.

Before major work:

```bash
git fetch upstream
git merge --no-ff upstream/main
```

or use the chosen rebase policy.

Keep Plandex core patches narrow.

When possible, add interfaces/hooks instead of copying whole subsystems.

Update external dependencies deliberately and run their integration smoke tests before advancing pins.


## 24. Repository URLs

Base:

```text
https://github.com/plandex-ai/plandex
```

Selective source vendor:

```text
https://github.com/bingreeky/JIT
```

Pinned runtime integrations:

```text
https://github.com/github/github-mcp-server
https://github.com/Graphify-Labs/graphify
https://github.com/alibaba/open-code-review
https://github.com/mostlygeek/llama-swap
https://github.com/ggml-org/llama.cpp
https://github.com/headroomlabs-ai/headroom
```

Model/reference repos:

```text
https://github.com/XHToken/Spark-X2.5
https://github.com/OpenBMB/MiniCPM
```

Optional external only:

```text
https://github.com/oraios/serena
```

### Final interpretation

"Merge the projects" means:

```text
Plandex
    = fork/base

JIT
    = selective vendored source because we adapt its behavior

GitHub MCP
Graphify
Open Code Review
llama-swap
llama.cpp
Headroom
    = pinned executable/package integrations

Spark
MiniCPM
    = model artifacts only

Serena
    = optional separate process only
```

Do not flatten all upstream repositories into a single source tree.

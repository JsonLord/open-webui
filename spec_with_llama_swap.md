# spec.md — Open WebUI + Plandex Agentic Coding System

## 0. Authority and status

This file is the new source of truth for `JsonLord/open-webui` on branch `integration-plandex`.
It supersedes `spec_with_llama_swap.md` and all earlier architecture assumptions that required local Spark or MiniCPM inference.

### Baseline model strategy

- **Spark-X2.5-1.7B**: remote inference through the already-working Hugging Face Space at `https://leon4gr45-llama.hf.space/`.
- **MiniCPM review model**: remote inference through a configurable OpenAI-compatible endpoint.
- **Needle3**: the only required local model in the main Hugging Face Space, used strictly for tool selection + typed argument generation.
- **Cloud meta-model**: used selectively for JIT long-horizon planning and replanning.

No baseline component requires `llama.cpp`, `llama-swap`, local Spark GGUFs, or local MiniCPM GGUFs. Those are optional future fallback mechanisms only.

---

## 1. Goal

Build a long-running agentic coding system inside the existing Open WebUI + Plandex monorepo without rebuilding a generic coding agent loop.

Reuse Plandex for:

- repository context and project maps;
- smart context selection;
- planner / architect / coder separation;
- builder and whole-file-builder editing;
- pending-change sandbox;
- apply/revert;
- command execution;
- test/debug/retry behavior;
- plan history;
- Git integration.

Add only the missing orchestration layers:

1. Open WebUI as the browser shell.
2. Thin control plane for tasks, scheduling, GitHub lifecycle, and status.
3. Headroom as the local context-compression proxy before Spark.
4. Remote Spark as the primary coding/reasoning model.
5. Needle3 as the local tool-choice + argument-generation specialist.
6. Deterministic schema and policy validation after Needle3.
7. GitHub MCP for GitHub-side context/actions.
8. Graphify for deeper structural repository reasoning.
9. JIT/cloud planner for long-horizon planning and replanning.
10. Open Code Review + remote MiniCPM for independent PR review.
11. Tasks / Reviews / Archive UI in Open WebUI.

The system must support multi-step, long-running coding work without expecting one small model to maintain strategic coherence for hours.

---

## 2. Target architecture

```text
                           Browser
                              |
                              v
                    Open WebUI :7860
                              |
                              v
                        Control Plane
                              |
          +-------------------+--------------------+
          |                   |                    |
          v                   v                    v
       Tasks/UI           Scheduler           GitHub state
          |                   |                    |
          +-------------------+--------------------+
                              |
                              v
                           Plandex
          +-------------------+--------------------+
          |                   |                    |
          v                   v                    v
      reasoning/code      tool intent        repo intelligence
          |                   |                    |
          v                   v                    v
      Headroom             Needle3              Graphify
          |                   |                    |
          v                   v                    |
   Remote Spark          ToolRouter               |
                              |                    |
                              v                    |
                   schema + policy validator      |
                              |                    |
                              v                    |
                         Tool Gateway <------------+
                              |
                 +------------+-------------+
                 |            |             |
                 v            v             v
             GitHub MCP    tests/git      Context7*
                 |
                 v
                PR
                 |
                 v
        Open Code Review
                 |
                 v
        Remote MiniCPM
                 |
                 v
          review findings
                 |
          repair if needed
                 |
                 v
        Plandex + Headroom + Spark
```

`Context7` is optional and should only be added when version-specific external documentation is needed.

---

## 3. Responsibility boundaries

### 3.1 Open WebUI

Open WebUI is presentation only.

Eventually expose:

```text
Tasks | Reviews | Archive
```

Optionally add `Repositories` on desktop.

Open WebUI must not become the agent orchestrator. It talks to the control plane through REST/SSE and renders state.

### 3.2 Control Plane

The control plane owns:

- task intake and state;
- priorities and cancellation;
- repository/org allowlists;
- Git branch/worktree lifecycle;
- GitHub issue/PR lifecycle;
- JIT invocation;
- Needle routing requests;
- Graphify invocation;
- review scheduling;
- repair scheduling;
- REST/SSE state for Open WebUI.

Suggested structure:

```text
app/controlplane/
  api/
  scheduler/
  tasks/
  github/
  jit/
  tools/
  needle/
  graphify/
  review/
  security/
  state/
```

Use existing repository conventions instead if they provide a cleaner integration point.

### 3.3 Plandex

Plandex remains the coding execution engine.

Plandex owns:

- repository context;
- project maps;
- smart context;
- planning/execution semantics;
- code-generation roles;
- builders;
- pending changes;
- apply/revert;
- command execution;
- test/debug loops;
- plan history.

Do not add another file-editing or generic retry loop in the control plane.

---

## 4. Headroom

Upstream:

```text
https://github.com/JsonLord/headroom
```

Headroom is mandatory on the primary Spark path.

Use **proxy mode**.

Primary path:

```text
Plandex
  -> Headroom
  -> remote Spark
```

Headroom is responsible for local compression of:

- final prompt/context payloads;
- tool outputs;
- logs;
- repetitive JSON;
- code/context where appropriate.

Headroom is not responsible for:

- model selection;
- tool execution;
- long-horizon planning;
- replacing Plandex smart context;
- replacing Graphify;
- Git/GitHub lifecycle.

Do not use `headroom wrap` in production integration.
Do not enable Headroom cross-agent memory, learning, Serena installation, or Headroom MCP in the baseline milestone.

Default local endpoint:

```text
HEADROOM_BASE_URL=http://127.0.0.1:8787/v1
```

Its upstream must be the verified remote Spark endpoint.

---

## 5. Remote Spark

Existing service:

```text
https://leon4gr45-llama.hf.space/
```

Do not assume the exact OpenAI-compatible route or model ID.
Implementation must probe and document the actual contract, including where supported:

```text
GET  /v1/models
POST /v1/chat/completions
```

Configuration:

```text
SPARK_BASE_URL=
SPARK_MODEL=
SPARK_API_KEY=
```

If no API key is required, allow an empty value.

Plandex should not call Spark directly. Plandex calls Headroom, and Headroom forwards to Spark.

Plandex roles initially map as:

```text
architect          -> cloud-capable model
planner            -> cloud-capable model
coder              -> Spark through Headroom
builder            -> Spark through Headroom
wholeFileBuilder   -> Spark through Headroom
autoContinue       -> Spark if reliable
commitMessages     -> Spark if reliable
summarizer         -> Spark if reliable, otherwise fallback
```

---

## 6. Needle3 — local ToolRouter

Model:

```text
Cactus-Compute/needle3
```

Needle3 is the only required local model in the baseline HF Space.
It is a specialist router, not a general reasoning agent.

### 6.1 Contract

Create a dedicated `ToolRouter` abstraction:

```text
route_tool(intent, candidate_tools, context)
  -> {
       tool,
       arguments,
       confidence
     }
```

Needle3 may choose a tool and generate its typed arguments, but it must never execute the tool directly.

Required pipeline:

```text
Needle3
  -> schema validation
  -> policy validation
  -> deterministic tool execution
```

### 6.2 Candidate-tool narrowing

Do not expose every tool on every step.
The current plan / capability family should narrow the candidate set first.

Examples:

```text
repository analysis:
- repo_search
- read_file
- graphify_query
- graphify_path
- graphify_explain

GitHub inspection:
- github_get_issue
- github_get_pr
- github_get_checks
- github_get_comments

validation:
- run_tests
- run_lint
- run_typecheck
- inspect_diff
```

### 6.3 Policy boundary

Do not expose high-risk lifecycle actions to Needle3 by default:

- force push;
- merge PR;
- delete branch;
- write protected branches;
- change repository permissions;
- expose secrets.

These remain deterministic control-plane operations.

### 6.4 Confidence and abstention

Keep thresholds configurable and calibrate them against the actual tool suite.

```text
high confidence
  -> validate + execute

uncertain
  -> ask Spark/JIT to clarify or choose deterministic fallback

abstain / no valid call
  -> return control to Spark/JIT
```

Disable telemetry in production where supported:

```text
NEEDLE_TELEMETRY=0
DO_NOT_TRACK=1
```

---

## 7. JIT / long-horizon planner

Use a cloud large-context model selectively for:

- initial meta-plan;
- task decomposition;
- capability-family selection;
- budget selection;
- checkpoint planning;
- replanning after repeated failure;
- architectural reassessment.

JIT should emit a typed execution profile, not arbitrary executable harness code.

Example:

```json
{
  "task_type": "bug_fix",
  "capabilities": ["repo_analysis", "github_read", "validation"],
  "context": {"graphify": true},
  "planning": {"max_steps": 12, "checkpoint_every": 3},
  "execution": {"max_debug_tries": 3},
  "validation": {"require_tests": true},
  "budgets": {"wall_seconds": 1800}
}
```

JIT never directly edits files.

---

## 8. Long-running execution loop

Use bounded execution segments and checkpoints.

```text
JIT / cloud planner
      |
      v
strategic plan
      |
      v
Plandex execution segment
      |
      +--> Spark reasoning/code through Headroom
      |
      +--> Needle3 tool routing
      |
      +--> deterministic tools
      |
      v
validation/checkpoint
      |
      +--> continue
      |
      `--> JIT replan
```

Replan when appropriate, including:

- execution segment completed;
- repeated test failure;
- repeated Needle abstention;
- diff expands beyond planned scope;
- unexpected subsystem/dependency discovered;
- task enters a new architectural area;
- review exposes architectural defects.

Keep triggers configurable.

---

## 9. Graphify

Upstream:

```text
Graphify-Labs/graphify
```

Do not merge its source tree.
Install a pinned runtime/package dependency.

Plandex project maps remain the default repository map.
Use Graphify only when deeper structural reasoning is needed:

- callers/callees;
- call chains;
- inheritance;
- dependency relationships;
- blast radius;
- pre-PR impact analysis.

Graphify is exposed through the Tool Gateway and may be selected by Needle3.

---

## 10. GitHub integration

Use:

```text
github/github-mcp-server
```

Run as a pinned local process.

Use GitHub MCP for:

- issue content;
- PR metadata;
- review threads;
- checks/actions state;
- repository metadata;
- GitHub-side actions where appropriate.

Use ordinary `git` for:

- clone/fetch;
- worktrees;
- branches;
- status;
- diff;
- commit;
- push.

Keep `gh` for deterministic fallback operations.

Canonical secret:

```text
GITHUB_PAT
```

Never expose the PAT to model prompts, logs, repository test commands, or UI responses.

---

## 11. GitHub-first task lifecycle

```text
1. validate repository against allowlist
2. load issue/task context
3. clone/fetch repository
4. create clean branch/worktree
5. create/open Plandex plan
6. build/update Plandex project map
7. optionally use Graphify
8. obtain JIT execution profile
9. execute bounded Plandex segment
10. use Needle3 for constrained tool routing
11. validate pending changes
12. apply changes
13. run final validation
14. commit
15. push
16. create/update PR
17. queue PR for review
```

Branch format:

```text
agent/<task-slug>-<short-id>
```

Never modify protected/default branches directly.

---

## 12. Code review

Use:

```text
alibaba/open-code-review
```

MiniCPM is remote.

Configuration:

```text
MINICPM_BASE_URL=
MINICPM_MODEL=
MINICPM_API_KEY=
```

Probe and document the actual remote endpoint contract during implementation.

Initial review path:

```text
PR
  -> Open Code Review
  -> remote MiniCPM
  -> structured findings
```

Do not route MiniCPM through Headroom initially.
MiniCPM reviews; it does not edit.

Repair path:

```text
findings
  -> Plandex repair task
  -> Headroom
  -> remote Spark
  -> tests
  -> commit/push
  -> re-review
```

Initial maximum automatic repair cycles: `2`, configurable.
Repeated failures become `needs-human-review`.

---

## 13. Baseline Hugging Face runtime

Target:

```text
Hugging Face Docker Space
CPU-only
```

Persistent/local components:

```text
Open WebUI
Plandex
PostgreSQL
Headroom
Needle3 runtime
Control Plane
GitHub MCP
Graphify
Open Code Review
```

External inference:

```text
Spark -> remote HF Space
MiniCPM -> remote inference endpoint
JIT/meta-planner -> cloud inference endpoint
```

No baseline requirement for:

```text
llama.cpp
llama-swap
local Spark GGUF
local MiniCPM GGUF
```

These may return later as optional offline/failure fallback only.

### Ports

Public:

```text
Open WebUI
0.0.0.0:7860
```

Internal defaults:

```text
Plandex
127.0.0.1:8099

Headroom
127.0.0.1:8787

PostgreSQL
loopback/internal only
```

Needle3 should be in-process where practical. If it requires a service process, bind loopback-only.
Only port 7860 is public.

---

## 14. Process supervision

Use one lightweight supervisor for the HF container.

Expected persistent processes:

1. PostgreSQL
2. Plandex
3. Headroom
4. GitHub MCP if persistent
5. Open WebUI

Needle3 may be embedded in the control plane or run as a local service depending on its runtime API.

Do not introduce Kubernetes, Docker-in-Docker, Redis, Celery, or systemd unless proven necessary.

Requirements:

- dependency-aware startup;
- readiness checks;
- structured logs;
- graceful SIGTERM/SIGINT;
- child cleanup;
- no infinite restart loops.

---

## 15. Control-plane API

Initial API:

```text
GET  /health
GET  /status

POST /tasks
GET  /tasks
GET  /tasks/{id}
POST /tasks/{id}/cancel

GET  /reviews
GET  /reviews/{id}
POST /reviews/{id}/repair

GET  /archive

GET  /repos
GET  /repos/{repo}/branches

GET  /tasks/{id}/diff
GET  /tasks/{id}/events
```

Use SSE for live task events where appropriate.
Do not expose arbitrary internal tools directly to the browser.

---

## 16. Open WebUI UX

Eventually adapt the Open WebUI fork to expose first-class coding views:

```text
Tasks | Reviews | Archive
```

Tasks should show task title, repo, branch, current step, execution status, tests, changed files, PR, and review status.

Reviews should show PR, review state, severity counts, findings, file/line, repair action, and re-review state.

Archive should show completed tasks, merged/closed PRs, failed tasks, and review history.

Do not implement these views before the backend task/review API is stable.

---

## 17. Persistence

Preserve Plandex persistence semantics initially.
Do not rewrite Plandex persistence just to simplify deployment.

GitHub remains the authoritative external record for branches, commits, PRs, and review state.

---

## 18. Security

- Repository allowlist required.
- Organization allowlist optional but recommended.
- No direct protected-branch mutation.
- Never send secrets to Spark, MiniCPM, Needle3, JIT, repo tests, or logs.
- Needle3 arguments must pass schema validation.
- Needle3 arguments must pass policy validation.
- Cloud/JIT shell commands must be allowlisted or generated deterministically.
- Destructive GitHub operations require explicit control-plane policy.

---

## 19. External dependency policy

Do not flatten external repositories into the Open WebUI repository.

### Already integrated

```text
Plandex
```

Plandex currently exists as copied source in the monorepo.

### Runtime/package dependencies

```text
JsonLord/headroom
Cactus-Compute/needle3
github/github-mcp-server
Graphify-Labs/graphify
alibaba/open-code-review
```

### Selective vendoring/adaptation

```text
bingreeky/JIT
```

Vendor only the minimum needed to produce the typed execution profile.

### Remote model services

```text
Spark-X2.5-1.7B
MiniCPM review model
cloud JIT/meta model
```

Track exact versions/pins where applicable in:

```text
third_party/versions.lock
docs/integration/DEPENDENCIES.md
```

---

## 20. Observability and evaluation

Track at least:

- task ID;
- repo;
- branch;
- Plandex plan ID;
- current phase/step;
- Spark request count and latency;
- Headroom compression ratio/tokens saved;
- Needle3 tool choice/confidence/abstentions;
- tool execution result;
- retries;
- test outcomes;
- review findings;
- repair cycles;
- final PR.

### Needle3 evaluation

Build a tool-routing evaluation set from real requests and measure:

- correct tool selection;
- argument exactness;
- invalid argument rate;
- abstention quality;
- confidence calibration;
- latency.

Persist privacy-safe routing traces for later evaluation/fine-tuning.

### Review evaluation

Measure useful findings, false positives, missed seeded bugs, repair success, and repeated finding rate.

---

## 21. Implementation phases

### Phase 0 — Reconcile the monorepo

- read this new spec;
- audit current Open WebUI + Plandex integration;
- identify stale llama.cpp/llama-swap/local-Spark assumptions;
- preserve useful existing work;
- remove/deprecate obsolete baseline config only after confirming no active dependency;
- update living docs.

Acceptance:

```text
repository documentation and code agree on the new remote-model architecture
```

### Phase 1 — Remote Spark + Headroom

- probe the existing Spark HF endpoint;
- document actual route/model ID;
- configure Headroom proxy;
- point Plandex Spark provider to Headroom;
- Headroom forwards to remote Spark;
- add config tests;
- add runtime smoke test.

Acceptance:

```text
Plandex
 -> Headroom
 -> remote Spark
 -> valid response
```

Then prove a small real Plandex code change.

### Phase 2 — Needle3 ToolRouter

- install Needle3 locally;
- disable telemetry;
- implement ToolRouter;
- define a small initial tool catalog;
- implement candidate-tool narrowing;
- implement schema validation;
- implement policy validation;
- implement confidence/abstention handling;
- add deterministic fixtures;
- add Needle evaluation harness.

Acceptance:

```text
intent
 -> Needle3
 -> valid tool + args
 -> validator
 -> deterministic execution
```

### Phase 3 — GitHub-first control plane

- `GITHUB_PAT` handling;
- repo/org allowlists;
- official GitHub MCP;
- Git worktrees;
- issue/PR context;
- branch creation;
- commit/push;
- PR creation;
- task state API.

Acceptance:

```text
GitHub issue
 -> task
 -> branch/worktree
 -> Plandex
 -> commit
 -> push
 -> PR
```

### Phase 4 — Graphify escalation

- pinned Graphify install;
- Tool Gateway integration;
- Needle3 Graphify schemas;
- query/path/explain;
- cache artifacts outside Git branch.

### Phase 5 — JIT long-horizon planning

- selectively adapt JIT;
- typed execution profile;
- capability-family selection;
- bounded execution segments;
- checkpoint/replan rules.

### Phase 6 — Remote MiniCPM review

- probe MiniCPM endpoint;
- configure Open Code Review;
- structured review output;
- repair queue;
- max repair cycles.

### Phase 7 — Open WebUI coding UI

Implement:

```text
Tasks
Reviews
Archive
```

backed by the control-plane API/SSE.

### Phase 8 — Optimization / fallback

Only after benchmarks:

- Headroom for MiniCPM;
- optional local Spark fallback;
- optional llama.cpp/llama-swap fallback;
- Context7;
- Serena;
- specialized Needle3 fine-tune;
- additional caching.

---

## 22. Living documentation

Maintain:

```text
docs/integration/ARCHITECTURE.md
docs/integration/IMPLEMENTATION_STATUS.md
docs/integration/DECISIONS.md
docs/integration/WORKLOG.md
docs/integration/NEXT_STEPS.md
docs/integration/DEPENDENCIES.md
```

Use exactly these status categories:

```text
IMPLEMENTED + VERIFIED
IMPLEMENTED — NOT RUNTIME VERIFIED
NOT IMPLEMENTED
```

Do not claim runtime verification when only configuration tests passed.

---

## 23. First Codex milestone

Codex starts with **Phase 0 + Phase 1 only**.

Do not add Needle3, GitHub MCP, Graphify, JIT, review, or coding UI during the first implementation pass.

First prove:

```text
Plandex
  -> Headroom
  -> remote Spark
```

using the existing Spark HF Space.

The following session should implement Needle3 ToolRouter.

---

## 24. Definition of architectural success

```text
Open WebUI
  -> control plane
  -> Plandex

Plandex reasoning/code
  -> Headroom
  -> remote Spark

Plandex capability intent
  -> local Needle3
  -> validated tool execution

long-horizon strategy
  -> cloud JIT planner

PR review
  -> Open Code Review
  -> remote MiniCPM
```

No baseline component depends on local Spark, local MiniCPM, `llama.cpp`, or `llama-swap`.

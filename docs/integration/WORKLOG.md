# Integration worklog

## 2026-09-23 — Phase 0 reconciliation and Phase 1 scaffolding

### Changed

- Reconciled the copied Plandex tree and existing Open WebUI runtime with the
  remote-model architecture.
- Added pinned Headroom proxy dependency and loopback-only startup.
- Added environment-driven Plandex provider/model-pack rendering.
- Added real (never mocked) direct-Spark and Headroom-through-Spark smoke tools.
- Added static routing tests and all mandatory living documentation.

### Files

`.env.example`, `spec.md`, `integration/`, `scripts/integration/`,
`test/test_spark_integration.py`, and `docs/integration/`.

### Tests

- `python -m unittest discover -s test -p 'test_spark_integration.py' -v` — passed (3 tests).
- `bash -n scripts/integration/common.sh scripts/integration/start-headroom.sh scripts/integration/wait-headroom.sh` — passed.
- `uv pip install --python .venv/bin/python -r integration/requirements.txt` — passed; installed Headroom 0.37.0.
- `PATH="$PWD/.venv/bin:$PATH" SPARK_API_KEY=placeholder-for-readiness-only scripts/integration/start-headroom.sh` — proxy started and exposed `/livez`, `/health`, and `/stats`; upstream health was correctly unhealthy because the network proxy returned 403.
- `HEADROOM_READY_TIMEOUT=30 scripts/integration/wait-headroom.sh` — passed; `/livez` reported Headroom 0.37.0 healthy and runtime config confirmed memory, learn, and code graph were disabled.
- JSON rendering/parsing and `git diff --check` — passed.
- Direct remote probes to `/`, `/v1/models`, `/models`, and `/health` — blocked
  before TLS by the environment CONNECT proxy with HTTP 403.
- Live inference — not run: `SPARK_API_TOKEN`/`SPARK_API_KEY` is unset.

### Problems

The requested `spec.md` was absent at session start; only the superseded local
llama-swap specification existed. The current architecture specification has
now been restored at `spec.md`. The checked-out branch was named `work`, no
remotes were configured, and no `integration-plandex` ref was present.

### Decisions

Remote Spark is mandatory, Headroom cannot be bypassed by Plandex, and
`SPARK_API_TOKEN` is supported only as a temporary alias.

### Remaining

The endpoint contract, streaming, timeout behavior, Headroom runtime, and a
real Plandex pending-change flow all require a credentialed reachable runtime.

## 2026-09-23 — Phase 1C Spark request contract

### Changed

- Added authoritative `spark-x2.5-1.7b` Q8_0 identity and explicit 32,768-token
  contract with a 24,576 input ceiling, 4,096 output ceiling, and 4,096 reserve.
- Added a dependency-free loopback request adapter on port 8790 after Headroom.
- Added parameter allowlisting, model normalization, conservative token
  estimation, structured overflow errors, bounded retry classification,
  configurable timeouts/defaults, model-list mismatch detection, and metrics.
- Changed Headroom's upstream from remote Spark to the contract adapter while
  preserving Plandex -> Headroom routing.

### Files

`integration/spark_contract.py`, `scripts/integration/spark-contract-server.py`,
Spark startup/readiness scripts, configuration, tests, specification, and all
living integration documents.

### Tests

- `python -m unittest discover -s test -p 'test_spark*.py' -v` — passed 17 tests.
- `bash -n scripts/integration/*.sh` — passed.
- `python -m py_compile integration/spark_contract.py scripts/integration/*.py` — passed.
- `SPARK_MODEL=spark-x2.5-1.7b scripts/integration/render-plandex-models.py | python -m json.tool >/dev/null` — passed.
- `SPARK_API_KEY=placeholder scripts/integration/start-spark-contract.sh` plus
  `scripts/integration/wait-spark-contract.sh` — passed; a live unsupported
  `seed` request returned structured HTTP 400 and `/metrics` counted the failure.
- `PATH="$PWD/.venv/bin:$PATH" SPARK_API_KEY=placeholder scripts/integration/start-headroom.sh` — passed; Headroom health reported OpenAI upstream `http://127.0.0.1:8790`, proving the structural order.
- `curl --max-time 20 https://leon4gr45-llama.hf.space/v1/models` — blocked before TLS by the environment CONNECT proxy with HTTP 403 (connect 0.004s, total 0.008s).
- `SPARK_MODEL=spark-x2.5-1.7b python scripts/integration/spark-smoke.py --via spark --timeout 20` — not executed upstream: credential check returned exit 2 because both supported secret variables were unset.
- `SPARK_API_KEY=placeholder SPARK_MODEL=spark-x2.5-1.7b python scripts/integration/spark-smoke.py --via headroom --timeout 5` — attempted; reached Headroom and its configured contract upstream, then timed out because the contract could not reach the remote Space.
- `git diff --check` — passed.

### Problems

No deployment credential is present. The environment proxy still rejects the
Space before TLS, so `/v1/models`, completion, streaming, parameter behavior,
and latency cannot be observed. A native Plandex lifecycle was not attempted
because its required inference chain cannot complete.

### Decisions

Final validation occurs after compression. The conservative fallback estimator
uses serialized UTF-8 bytes/3 plus framing and is explicitly not an exact Spark
tokenizer. No prompt is silently truncated.

### Remaining

Run direct, adapter, Headroom, streaming, parameter, and native Plandex checks
in a credentialed environment that can reach the Space. Compare rather than
silently replace the authoritative model ID if `/v1/models` differs.

## 2026-09-23 — Phase 1C adapter review hardening

### Changed

- Made the 32,768-token physical ceiling impossible to raise through an
  environment override.
- Rejected booleans masquerading as numeric sampling values and validated the
  upstream URL shape at startup.
- Added final serialized request-byte metrics, response token-estimate headers,
  invalid-JSON model-list handling, and consistent failure counting.
- Added process-level adapter tests against a local fake Spark upstream for
  model discovery, sanitized non-streaming forwarding, SSE preservation, and
  pre-upstream rejection.

### Files

`integration/spark_contract.py`, `scripts/integration/spark-contract-server.py`,
`test/test_spark_adapter.py`, `test/test_spark_contract.py`, and living docs.

### Tests

- `python -m unittest discover -s test -p 'test_spark*.py' -v` — passed 24 tests.
- `curl -sS --max-time 20 https://leon4gr45-llama.hf.space/v1/models` — attempted
  again; CONNECT proxy returned HTTP 403 before TLS (curl exit 56, connect
  0.003s, total 0.006s).
- `SPARK_API_KEY` and `SPARK_API_TOKEN` checks — both unset.

### Problems

The external connectivity and credential blockers remain unchanged. Local
transport verification is not represented as successful live Spark inference.

### Decisions

The physical model ceiling may be lowered for deployment testing but never
raised. A fake HTTP upstream is appropriate for deterministic adapter transport
tests; the live smoke suite remains separate and credential-gated.

### Remaining

Credentialed direct and full-chain inference, streaming, parameter acceptance,
latency measurement, and native Plandex pending-change verification.

## 2026-09-23 — Phase 2 local Needle3 ToolRouter core

### Changed

- Inspected `Cactus-Compute/needle` at
  `42bf1f2d0a7784b0d4d1ec94bb5ade425cf9a67c` and pinned the published
  inference-only `cactus-needle==3.0.1` package.
- Added a ToolRouter that sends at most five caller-selected schemas to local
  Needle3 through `complete()` only; it never calls upstream `run()`.
- Added JSON Schema validation, confidence/confirmation gates, role/effect
  policy, suppressed-call handling, abstention, and fail-closed model-output
  validation.
- Added a separate ToolGateway that invokes only registered deterministic
  handlers and refuses abstained, confirmation, or rejected decisions.

### Files

`integration/tool_router.py`, `scripts/integration/needle-smoke.py`,
`test/test_tool_router.py`, `.env.example`,
`integration/requirements.txt`, `spec.md`, and living integration documents.

### Tests

- `uv pip install --python .venv/bin/python 'cactus-needle==3.0.1'` — passed.
- `uv pip install --python .venv/bin/python 'jsonschema==4.26.0'` — passed.
- `uv pip install --python .venv/bin/python -r integration/requirements.txt` — passed; resolved the pinned Headroom, Needle, and schema-validation runtime together.
- `python -m unittest discover -s test -p 'test_*router.py' -v` — passed 18 tests.
- `python -m unittest discover -s test -p 'test_spark*.py' -v` — passed 24 regression tests.
- `python -m py_compile integration/tool_router.py` — passed.
- `python -m black --check integration/tool_router.py test/test_tool_router.py` — passed.
- `NEEDLE_TELEMETRY=0 DO_NOT_TRACK=1 .venv/bin/python scripts/integration/needle-smoke.py`
  — attempted; returned `{"status":"error","error":"403 Forbidden"}` while
  downloading the platform engine from Hugging Face. No local inference was
  claimed.

### Problems

The published package installed, but this environment cannot download the
matching engine and `needle3.cact` assets. The inspected upstream default branch
also contains unreleased API evolution beyond the published 3.0.1 signature;
the adapter intentionally uses only the published common API.

### Decisions

Candidate selection remains deterministic application logic. Needle receives a
small candidate set and returns a proposal; schema and policy validation remain
outside the model, and execution remains in a separate gateway. Telemetry is
disabled by default.

### Remaining

Bake the pinned engine/weights in a network-enabled build, runtime-test
confidence and abstention, then integrate candidate selection and audit logging
when the control plane exists.

## 2026-09-23 — Phase 2B offline Needle runtime packaging

### Changed

- Inspected the installed 3.0.1 package and recorded generation 3, engine 3.0.1,
  `Cactus-Compute/needle3`, `needle3.cact`, cache layout, glibc
  `manylinux2014_x86_64` wheel path, and extracted `libneedle.so` behavior.
- Corrected the base/custom distinction: stock routing omits `weights=` and
  retains in-process loaded engine/base state; only `NEEDLE_CUSTOM_WEIGHTS`
  enters the tuned worker path.
- Added fail-closed, immutable-revision/SHA256 artifact acquisition, an x86_64
  glibc Docker build fragment, offline startup preflight, and non-sensitive
  runtime status.
- Serialized stock inference around upstream's process-global native state and
  rejected high-confidence responses marked ungrounded or negated.
- Added real-model offline, benchmark, and 30-case evaluation harnesses. They
  remain opt-in/unexecuted until verified artifacts exist.

### Files

`integration/needle_runtime.py`, `integration/Dockerfile.needle`,
`third_party/versions.lock`, artifact/preflight/benchmark/evaluation scripts,
`integration/needle-eval-cases.json`, runtime/router tests, configuration,
specification, and living documentation.

### Tests

- Installed-package inspection — verified `cactus-needle 3.0.1`, generation 3,
  engine 3.0.1, repo `Cactus-Compute/needle3`, `needle3.cact`, cache
  `/root/.cache/cactus-needle/v3/3.0.1`, platform
  `manylinux2014_x86_64`, `libneedle.so`, and glibc 2.39.
- `python -m unittest discover -s test -p 'test_*needle*.py' -v` — passed seven,
  skipped installed-package and real-offline tests when the integration package
  was absent from the system interpreter.
- `.venv/bin/python -m unittest discover -s test -p 'test_*needle*.py' -v` —
  passed eight; skipped only the real-offline test because artifacts are absent.
- `python -m unittest discover -s test -p 'test_*router.py' -v` — passed 21 tests.
- Phase 1 Spark regression suite remains required before completion.
- `.venv/bin/python scripts/integration/needle-benchmark.py --iterations 5` —
  attempted and failed closed because the immutable artifact contract is
  unresolved; no performance values recorded.
- `.venv/bin/python scripts/integration/needle-evaluate.py` — attempted and
  failed closed for the same unresolved pins; no accuracy or confidence values
  recorded.
- Artifact resolution/download was attempted previously through installed
  Needle initialization and failed with CONNECT proxy HTTP 403; no revision,
  checksum, size, inference, latency, memory, accuracy, or calibration result is
  fabricated.

### Problems

Hugging Face cannot be reached, and its cache contains no prior Needle artifact.
Therefore immutable model revision and SHA256 values remain explicit
`UNRESOLVED_NETWORK_BLOCKED` markers. The Docker fragment intentionally fails
closed until real pins are supplied.

### Decisions

Use the package's supported cache layout rather than monkey-patching it. Use
fresh schema sessions to guarantee independent routes while relying on verified
upstream process-global library/base loading. Do not add a schema cache without
measurements. Keep 512 output tokens, five candidates, and provisional
0.70/0.30 thresholds until real evaluation data exists.

### Remaining

Resolve pins, bake artifacts, prove network-free inference, collect artifact
sizes/RSS/cold and warm 1/3/5 latency, run all 30 real cases, inspect confidence,
and calibrate thresholds. Phase 3 remains blocked on Phase 2B completion.

## 2026-09-23 — Needle artifact closure and Phase 3 foundation

### Changed
- Pinned the immutable Needle model revision, model checksum, and downloaded engine-wheel checksum; renamed the ambiguous engine checksum and verify the wheel before extraction.
- Added an authenticated Open WebUI control-plane module with typed tasks, validated states, ordered events, PostgreSQL storage, allowlists, Git worktrees, cancellation, idempotency, health, native Plandex adapter, and SSE API.
- Pinned official GitHub MCP v1.12.2 at `85598ba6e1256f7ebf4867b95d63b833c4549264`, read-only with four toolsets and `GITHUB_PAT` as canonical secret.

### Tests
- `python -m unittest discover -s test -p 'test_control_plane.py' -v`: 14 tests passed, including a real temporary bare-repository/worktree lifecycle.
- `python scripts/integration/fetch-needle-artifacts.py`: failed with HTTP 403 from the environment proxy; artifact identity is resolved but acquisition remains blocked.

### Problems
- No `GITHUB_PAT` is present, so no live GitHub MCP read-only smoke was attempted.
- PostgreSQL/Plandex/remote Spark and real offline Needle runtime remain unverified; deterministic contracts do not imply runtime success.

### Decisions
- Reuse authenticated Open WebUI FastAPI and PostgreSQL rather than adding another public service/database.
- Keep local Git and remote GitHub MCP responsibilities separate; preserve task worktrees on cancellation.

### Remaining
- Runtime-test the PostgreSQL schema, official MCP initialization/tool calls, native Plandex planning, and full issue-to-plan flow in the deployment environment.

### Final verification
- `python -m unittest discover -s test -p 'test_control_plane.py' -v`: 14 passed.
- `python -m unittest discover -s test -p 'test_needle_runtime.py' -v`: 8 passed, 1 installed-package check skipped because `cactus-needle` is absent from the active interpreter.
- `python -m unittest discover -s test -p 'test_*router.py' -v`: 21 passed.
- `python -m unittest discover -s test -p 'test_spark*.py' -v`: 24 passed.
- `python -m ruff check backend/open_webui/control_plane backend/open_webui/routers/control_plane.py test/test_control_plane.py integration/needle_runtime.py scripts/integration/fetch-needle-artifacts.py test/test_needle_runtime.py`: passed.
- `python -m py_compile backend/open_webui/control_plane/*.py backend/open_webui/routers/control_plane.py integration/needle_runtime.py scripts/integration/fetch-needle-artifacts.py`: passed.
- `bash -n scripts/integration/*.sh`: passed.
- `python -m json.tool third_party/versions.lock` and evaluation-case JSON parsing: passed.
- `git diff --check`: passed.
- Pinned GitHub MCP source/tag inspection succeeded without credentials. A local source-build attempt began resolving the pinned module but did not complete within the command window because the installed Go 1.24 tool downloaded the upstream-required Go 1.26 toolchain; no runtime claim is made.
- Live GitHub MCP smoke was not run because `GITHUB_PAT` is not set.

## 2026-09-23 — Phase 3 review hardening

### Changed
- Made task creation schedule the bounded initial lifecycle through GitHub issue loading, worktree preparation, and native Plandex plan creation with a single in-process worker lock.
- Added bounded MCP response reads, initialize handling, structured issue parsing, and clean child termination.
- Stopped treating human `plandex new` output as a plan UUID; plan identity remains unset until a stable native identifier is available.
- Made PostgreSQL task creation/event insertion and state/event transitions atomic, serialized event sequence allocation, and added durable repository registrations.
- Upgraded SSE from a one-shot snapshot to a resumable live stream with `Last-Event-ID`, keepalives, disconnect handling, and terminal completion.
- Made the PostgreSQL-only production requirement explicit with a normalized 503 on unsupported database backends.

### Tests
- `python -m unittest discover -s test -p 'test_control_plane.py' -v`: 17 passed, including real Git worktrees, MCP issue parsing, native Plandex invocation semantics, and durable schema shape.

### Problems
- PostgreSQL, GitHub MCP authentication, and native Plandex runtime remain unavailable in this environment; these deterministic improvements are not represented as live verification.

## 2026-09-23 — Phase 3D real runtime closure attempt

### Changed
- Added conservative restart recovery: non-terminal PostgreSQL tasks atomically become `FAILED/recovery_required` with an ordered event.
- Added a real PostgreSQL integration suite for restart durability, repository persistence, concurrent event sequencing, and concurrent idempotent creation.
- Pinned the GitHub MCP build toolchain to exact Go 1.25.12 and added a multi-stage runtime image containing only the binary.
- Corrected the live MCP issue tool name to canonical `issue_read` with `method=get` after inspecting real `tools/list`.
- Tightened aggregate health so unavailable baseline components cannot be hidden behind `ok=true`, and exposed separate MCP authentication and Needle artifact state.
- Added recognizable-fake-token regression coverage for Plandex/Git subprocess environments and persisted task/event data.

### PostgreSQL runtime
- Installed PostgreSQL 16.15 and used `postgresql://<user>@127.0.0.1:5432/<database>` with the production `PostgresTaskStore`.
- `CONTROL_PLANE_TEST_DATABASE_URL='<redacted PostgreSQL URL>' python -m unittest discover -s test -p 'test_control_plane_postgres_live.py' -v`: 3 passed.
- Verified tables `repositories`, `task_events`, `tasks`; primary keys; unique `(task_id, sequence)`; and unique `idempotency_key`.
- A new store/engine retrieved the same task, events, and repository registration, then atomically recovered an interrupted task to `FAILED/recovery_required` with sequence continuity.
- 24 concurrent event appends produced sequences 1–25 without collision; 16 concurrent creates with one idempotency key produced one task and one initial event.

### GitHub MCP runtime
- Pinned `go.mod` declares Go 1.25.12; `GOTOOLCHAIN=go1.25.12 go version` returned `go1.25.12 linux/amd64`.
- `GITHUB_MCP_OUTPUT=/opt/integration/bin/github-mcp-server scripts/integration/build-github-mcp.sh` succeeded.
- Binary reported v1.12.2 and commit `85598ba6e1256f7ebf4867b95d63b833c4549264`; executable size was 21,090,488 bytes for this build.
- Real stdio initialize/initialized/tools-list succeeded with a recognizable fake token. It returned 25 tools and no tool name containing create/delete/merge/update/push.
- A real PAT was not present; no authenticated GitHub API request was attempted.

### Open WebUI/API/SSE runtime
- Started the actual backend with PostgreSQL on `127.0.0.1:8088`; unauthenticated health/status/tasks/repos returned 401.
- Created a real admin session through the existing signup API; authenticated health/status/tasks/repos returned 200.
- PostgreSQL-backed SSE returned `text/event-stream`, IDs 1–6, structured JSON, and terminal completion. Reconnect with `Last-Event-ID: 4` returned only IDs 5–6.
- A non-terminal PostgreSQL task emitted repeated keepalives; client timeout/disconnect left the backend healthy. Two HTTP cancel requests both returned `CANCELLED`.
- Backend restart preserved the real task, all events, and repository registration.

### Real Git/worktree task
- POSTed an authenticated direct-prompt task for `JsonLord/open-webui`, base `integration-plandex`.
- Authorization preceded cloning. Credential URL, GitLab URL, path traversal, and a non-allowlisted GitHub repository were rejected without increasing the repo-cache count.
- Resolved base SHA `9a67af27865d86427546bdacf852136f7d54a812` and created `agent/create-a-small-test-plan-for-this-reposi-de9c76fc` beneath the configured worktree root.
- Repeated production worktree preparation returned the identical branch, path, and SHA.

### Plandex runtime blocker
- Built the actual Plandex server and CLI from repository source.
- Starting either binary attempted `https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken` during shared package initialization and failed with CONNECT proxy HTTP 403.
- Restarted Open WebUI with the real CLI on PATH and created another real task. Native `plandex new --name agent-7fb777b0 --context-dir .` was attempted; the task ended `FAILED` with normalized `last_error=plandex_unavailable`, no raw panic in API/SSE, and its worktree preserved.
- Because Plandex could not initialize, `plandex current`, plan persistence, Headroom/Spark execution, commit, push, and PR were not reached.

### Remaining
- Pin and bake the Plandex tokenizer cache asset, then repeat native plan creation. Stay in Phase 3; do not begin Graphify yet.
- `python -m unittest discover -s test -p 'test_control_plane.py' -v`: 20 passed after adding restart recovery, fake-token leakage, and real owned process-group cancellation coverage; an unrelated child remained alive.
- `PYTHONWARNINGS=error::ResourceWarning GITHUB_MCP_BINARY=/opt/integration/bin/github-mcp-server python -m unittest discover -s test -p 'test_github_mcp_live.py' -v`: 1 passed; initialize/initialized/tools-list and read-only surface verified without leaking pipe handles.
- `python -m ruff check backend/open_webui/control_plane backend/open_webui/routers/control_plane.py test/test_control_plane.py test/test_control_plane_postgres_live.py test/test_github_mcp_live.py integration/needle_runtime.py scripts/integration/fetch-needle-artifacts.py test/test_needle_runtime.py`: passed.
- `python -m py_compile backend/open_webui/control_plane/*.py backend/open_webui/routers/control_plane.py test/test_control_plane_postgres_live.py test/test_github_mcp_live.py`: passed.
- `bash -n scripts/integration/*.sh`, JSON validation, and `git diff --check`: passed.

## 2026-09-24 — Phase 3E targeted tokenizer packaging

### Changed
- Added a standard-library-only pinned `o200k_base` fetch/install/preflight contract and a minimal Docker build fragment.
- Added Plandex child preflight and preserved `TIKTOKEN_CACHE_DIR` while continuing to strip GitHub credentials.
- Added deterministic offline tests for cache-key derivation, integrity, atomic install, missing/corrupt/wrong-key artifacts, and child environment propagation.

### Tests and bootstrap diagnosis
- Environment proxy variables pointed to `http://proxy:8080`; pip had no configured index override, npm inherited the HTTPS proxy, and Git had no global proxy entry.
- One direct request with all proxy variables removed failed with `[Errno 101] Network is unreachable`; no dependency installation was attempted afterward.
- Python 3.12.13 already had FastAPI 0.136.3, SQLAlchemy 2.0.50, and requests 2.34.2; `.venv/bin/python` also existed. No Python or npm bootstrap was needed.
- The failed generic bootstrap's `No matching distribution found for fastapi==0.136.3` was secondary to unreachable `proxy:8080`. Its traversal into `plandex/docs` and `npm ci` was unrelated to Phase 3E and is explicitly excluded.

### Problems
- Canonical tokenizer bytes are not present locally. Both proxied access and the one proxy-free connectivity test are unavailable, so live fetch, offline Plandex initialization, `plandex new`, and `plandex current` remain blocked on artifact bytes.

## 2026-09-24 — Phase 3F pinned mirror acquisition

### Changed
- Recorded `rmusser01/tldw_chatbook` commit `b0dadf19414f5f8faf69b854d8e59007d275083e` as a transport-only mirror for the canonical OpenAI tokenizer artifact.
- Made the immutable raw URL the Docker build default while retaining canonical OpenAI URL-derived cache identity and canonical SHA256 verification.

### Runtime verification
- The existing fetcher downloaded the pinned raw GitHub artifact without a Git fallback.
- Observed size: 3,613,922 bytes.
- Observed SHA256: `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d` (canonical match).
- Installed at `/opt/integration/tiktoken-cache/fb374d419588a4632f3f557e76b4b70aebbca790` with mode `0444`; preflight passed.
- Built the native Plandex CLI and server. `plandex --help` and `plandex new --help` initialized successfully while the canonical Azure endpoint remained inaccessible, proving cached tokenizer initialization without fallback.
- A real `plandex new --name agent-phase3f --context-dir .` invocation reached the next independent boundary: a pristine CLI home requires interactive local-host authentication and exited on EOF. The self-hosted PostgreSQL/Plandex server and prior authenticated Open WebUI runtime are not provisioned in this fresh environment, so the authenticated API plan flow, `plandex current`, and restart persistence were not claimed.

## 2026-09-24 — Phase 3G native local auth and plan lifecycle

### Changed
- Added native `plandex sign-in --local-host` and `--validate-only`, reusing the existing local server account/sign-in/session/org code.
- Hardened native auth/account files to 0600 and the Plandex home to 0700.
- Added deployment scripts for loopback server startup, readiness, tokenizer-first auth bootstrap, native validation, and non-sensitive status.
- Mapped `PLANDEX_CLI_HOME` to child `HOME`, closed worker stdin, and added structured `plandex current --json` identity.
- Persisted separate plan ID, name, project ID, and current-verification fields and events.

### Runtime
- Installed PostgreSQL 16.15 and created dedicated database `plandex`; native migrations ran and remained up-to-date after restart.
- Ran the server with `GOENV=development`, `LOCAL_MODE=1`, loopback port 8099, persistent `/tmp/phase3g-server`, and the pinned LiteLLM 1.72.6 proxy. `/health` returned 200. Initial startup including proxy import took approximately 9 seconds.
- With stdin closed, first bootstrap created the native `local-admin@plandex.ai` user and `Local Org`; repeated bootstrap validated auth without prompting. Database counts remained one user and one org. Auth survived server restart.
- The authenticated Open WebUI API created task `1a123b86-196b-4ff8-a5fe-322cb3a44ecb`, prepared its contained worktree, and created native plan `1839bfeb-62c7-4f92-b1bb-6bb83db5b6ec`, name `agent-1a123b86`, project `bb13fbf9-7ade-437d-acd7-8020ce2ed078`, branch `main`.
- PostgreSQL persisted that structured identity and event. After Open WebUI restart the conservative recovery correctly marked the still-PLANNING task `FAILED/recovery_required`, while all plan identity fields remained. After Plandex restart, `current --json` returned the identical native identity.
- `plandex tell` was not attempted; Spark remains a separate unverified external boundary.

## 2026-09-24 — Phase 4A Graphify repository-intelligence foundation

### Changed
- Inspected and pinned `Graphify-Labs/graphify` v0.9.67 / commit `4c735618…`.
- Added an isolated code-only Graphify runtime and a normalized repository graph adapter.
- Added immutable repository+commit+version index identity, persistent manifests,
  atomic promotion, same-index locking, explicit lifecycle state, and dirty-worktree stale detection.
- Triggered optional graph creation after authorized repository preparation and
  exposed only privacy-safe task/health metadata.
- Added normalized symbol, neighborhood, dependencies, dependents, and summary queries.

### Files
- `backend/open_webui/control_plane/graph.py`
- `backend/open_webui/control_plane/{domain,service,health}.py`
- `backend/open_webui/routers/control_plane.py`
- `integration/{requirements-graphify.txt,Dockerfile.graphify-runtime}`
- `test/test_graphify.py`
- `.env.example`, `third_party/versions.lock`, `spec.md`, and living integration docs

### Tests
- `python -m unittest discover -s test -p 'test_graphify.py' -v` — 11 deterministic tests passed.
- Real Graphify smoke used the pinned 0.9.67 CLI, an allowlisted Git worktree,
  a persistent index root, a meaningful Python `calls` relationship query, and
  a fresh adapter instance for restart recovery — passed.
- No Spark, Headroom, Needle, GitHub PAT, or Plandex inference operation ran.

### Problems
- Graphify was not previously pinned or installed. Only the targeted isolated
  `graphifyy==0.9.67` runtime was installed for inspection/smoke; no monorepo-wide bootstrap ran.

### Decisions
- Base indexes represent exact immutable commits and are shared across tasks.
- Dirty worktrees become `STALE`; upstream incremental refresh is not claimed yet.
- Graph failure is optional supporting infrastructure and never corrupts/fails Git preparation.

### Remaining
- Phase 4B: carefully select bounded graph results for future Plandex context enrichment.
- Benchmark task-specific refresh before adopting Graphify's incremental path.

## 2026-09-25 — Phase 4B bounded Graphify context enrichment

### Changed
- Added typed GraphContext requests, normalized contexts, deterministic anchor
  extraction, deduplication, ranking, safe-path validation, strict budgets, and
  a trust-boundary Markdown renderer.
- Added non-critical task orchestration after native plan creation, compact
  events/metadata, base-revision current/stale semantics, and hash idempotency.
- Added native named stdin context loading plus JSON context listing; explicitly
  disabled Graphify 0.9.67 query logging.
- Added deterministic tests and a local relevance/efficiency evaluation harness.

### Files
- `backend/open_webui/control_plane/graph_context.py`
- `backend/open_webui/control_plane/{adapters,domain,service,graph}.py`
- `plandex/app/cli/{cmd,lib,types}` and `plandex/app/server/handlers`
- `test/test_graph_context.py`
- `scripts/integration/graph-context-evaluate.py`
- `integration/graph-context-eval-cases.json`
- `.env.example`, `spec.md`, and living integration documents

### Tests
- `python -m unittest discover -s test -p 'test_graph_context.py' -v` — 12 tests passed.
- `python -m unittest discover -s test -p 'test_graphify.py' -v` — 11 tests passed.
- `python -m unittest discover -s test -p 'test_control_plane.py' -v` — 20 tests passed.
- `TIKTOKEN_CACHE_DIR=/tmp/tiktoken-cache go test ./handlers` — passed after verified tokenizer fetch.
- `go test ./cmd ./lib` — passed.
- Real Graphify fixture: relevant-file recall 1.0, relevant-symbol recall 1.0,
  irrelevant files 0, 1,736 bytes, 579 estimated tokens, three queries, 3.888 ms
  evaluation latency (single case, so p50=p95=3.888 ms).

### Problems
- A first single-character `B` query exposed overly broad substring matching and
  included `unrelated.py`. Short anchors now require exact symbol/file-stem
  matches; the repeated real fixture excluded the unrelated subsystem.
- The native CLI and new flags build, but this container has no running
  PostgreSQL/Plandex server or persisted authenticated CLI home. A real load was
  attempted and stopped at the expected missing-auth/runtime boundary. No native
  context success or `ls` verification is claimed here.

### Decisions
- Structural context is native plan context, never task instruction.
- Named piped context is used instead of a worktree file or model-generated name.
- Enrichment failure is optional and cannot fail plan creation.
- Smart/auto context and project maps remain enabled and authoritative.

### Remaining
- Repeat native `load --name` / `ls --json` in the prepared Phase-3 runtime.
- Once verified, begin Phase 5 JIT planning/checkpoints; do not add post-edit
  Graphify refresh or model-routed Graphify queries in this increment.

## 2026-09-25 — Phase 3G auth hardening follow-up

- Added an explicit supervisor readiness gate that waits for the loopback
  Plandex health endpoint before tokenizer and native local-auth preflights.
- Native auth writes now tighten pre-existing `auth.json` and `accounts.json`
  files to 0600, rather than relying on create-time modes alone.
- The copied CLI preserves an explicitly configured read-only
  `TIKTOKEN_CACHE_DIR`, preventing task startup from replacing the deployment
  cache location with its private home cache.
- Central bounded-error redaction now covers Plandex bearer and JSON token
  forms without reading or logging native auth state.
- This checkout has no PostgreSQL client/server, Plandex binaries, or installed
  tokenizer cache, and Go dependency downloads return HTTP 403. Consequently,
  no new live server/auth/plan/restart verification is claimed by this
  follow-up; the deterministic tokenizer tests passed and runtime checks remain
  represented by the previously recorded deployment verification.

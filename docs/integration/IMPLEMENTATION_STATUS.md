# Implementation status

| Component | Status | Location | Verified | Notes |
|---|---|---|---|---|
| Open WebUI | IMPLEMENTED + VERIFIED | `src/`, `backend/open_webui/` | Real backend on `127.0.0.1:8088` | Authenticated and unauthenticated control-plane requests verified |
| Control-plane logic | IMPLEMENTED + VERIFIED | `backend/open_webui/control_plane/` | 20 deterministic tests plus runtime flow | State, events, recovery, cancellation, and failure normalization verified |
| PostgreSQL persistence | IMPLEMENTED + VERIFIED | `control_plane` PostgreSQL schema | PostgreSQL 16.15 live tests | Restart persistence, repository registrations, concurrent events, idempotency race verified |
| FastAPI control-plane runtime | IMPLEMENTED + VERIFIED | `/api/v1/control-plane` | Real Open WebUI process | Health/status/tasks/repos and auth behavior verified |
| SSE task events | IMPLEMENTED + VERIFIED | `routers/control_plane.py` | PostgreSQL-backed HTTP stream | IDs, JSON, keepalive, disconnect survival, terminal close, Last-Event-ID resume verified |
| Git repository preparation | IMPLEMENTED + VERIFIED | `control_plane/git.py` | Real `JsonLord/open-webui` clone | Base `9a67af27…`, contained worktree, agent branch, idempotent reuse verified |
| GitHub MCP binary | IMPLEMENTED + VERIFIED | `/opt/integration/bin/github-mcp-server` | v1.12.2/`85598ba6…` built with Go 1.25.12 | Runtime image stage contains binary only |
| GitHub MCP protocol | IMPLEMENTED + VERIFIED | `control_plane/adapters.py` | Real stdio initialize + tools/list | 25 read-only tools; no create/update/delete/merge/push tools |
| GitHub live authentication | IMPLEMENTED — NOT RUNTIME VERIFIED | `GITHUB_PAT` contract | PAT absent | No live GitHub API call claimed |
| Plandex adapter | IMPLEMENTED + VERIFIED | `control_plane/adapters.py` | Real binary invocation attempted | Correct deterministic argv and normalized failure verified |
| Plandex tokenizer packaging | IMPLEMENTED + VERIFIED | `backend/open_webui/control_plane/plandex_tokenizer.py` | Pinned mirror + live preflight | 3,613,922 verified bytes installed read-only with canonical SHA256 |
| Plandex offline tokenizer initialization | IMPLEMENTED + VERIFIED | native CLI/server | Real native CLI initialization | CLI help passed from baked cache while canonical endpoint remained inaccessible |
| Plandex server runtime | IMPLEMENTED + VERIFIED | `plandex/app/server` | PostgreSQL 16.15 + real server `/health` | Native migrations, loopback binding, persistent base, LiteLLM, and restart verified |
| Plandex non-interactive local auth | IMPLEMENTED + VERIFIED | copied CLI + bootstrap script | Real first/repeat/restart flow | Native server created session/account/org; no TTY; private persisted files |
| Plandex plan creation | IMPLEMENTED + VERIFIED | authenticated task worker | Real API task | Native deterministic-name plan created in contained worktree |
| Plandex current-plan retrieval | IMPLEMENTED + VERIFIED | `plandex current --json` | Real native API | Stable plan/name/project/branch identity verified |
| Plandex plan persistence | IMPLEMENTED + VERIFIED | PostgreSQL task payload/events | Backend and server restart | Structured identity survived and matched native current state |
| Spark-backed Plandex execution | IMPLEMENTED — NOT RUNTIME VERIFIED | Headroom/Spark integration | Not attempted after Plandex blocker | Phase 1C remains not fully runtime verified |
| Commit/push | NOT IMPLEMENTED | — | No generated change | Deferred until real execution and validation succeed |
| PR creation | NOT IMPLEMENTED | — | No real commit | Deliberately not added merely to satisfy a checkbox |
| Headroom | IMPLEMENTED — NOT RUNTIME VERIFIED | `integration/requirements.txt`, scripts | Local process previously verified | Remote inference chain remains blocked |
| Spark request contract | IMPLEMENTED — NOT RUNTIME VERIFIED | `integration/spark_contract.py` | 24 deterministic/local integration tests | Live Space unverified |
| Remote Spark | IMPLEMENTED — NOT RUNTIME VERIFIED | environment/smoke script | Proxy/credential blocked | Model contract remains authoritative |
| Needle3 | IMPLEMENTED — NOT RUNTIME VERIFIED | `integration/tool_router.py`, `needle_runtime.py` | Deterministic tests | Artifact download still returns proxy 403 |
| Graphify packaging/runtime | IMPLEMENTED + VERIFIED | `integration/Dockerfile.graphify-runtime`, `requirements-graphify.txt` | Graphify 0.9.67 real local CLI | Isolated code-only runtime; no LLM dependency |
| Graphify indexing | IMPLEMENTED + VERIFIED | `control_plane/graph.py` | Deterministic suite + real authorized-worktree smoke | Immutable commit-keyed persistent index, reuse and locking verified |
| Graphify structural queries | IMPLEMENTED + VERIFIED | `control_plane/graph.py` | Real `contains`/`calls` graph query | Normalized find/neighborhood/dependency/dependent/summary operations |
| Graphify persistence | IMPLEMENTED + VERIFIED | `$CONTROL_PLANE_DATA_DIR/graphs` | Restart/reuse tests | Exact-revision native graph and control-plane manifest |
| GraphContext enrichment | IMPLEMENTED + VERIFIED | `control_plane/graph_context.py` | Deterministic + real Graphify fixture | Exact-revision anchors, ranking, deduplication, provenance, safe paths |
| GraphContext budgeting | IMPLEMENTED + VERIFIED | `control_plane/graph_context.py` | Boundary/adversarial tests | Query/node/edge/file/byte/token caps enforced |
| GraphContext -> Plandex loading | IMPLEMENTED — NOT RUNTIME VERIFIED | `PlandexExecutor.load_structural_context` | Adapter + Go tests; live attempt blocked before server | Named stdin context, hash idempotency, native list verification implemented |
| Native Plandex context verification | IMPLEMENTED — NOT RUNTIME VERIFIED | `plandex load --name`, `plandex ls --json` | CLI built; no running persisted Phase-3 server/home in this environment | Requires deployment runtime rerun; no Spark required |
| Post-edit Graphify refresh | NOT IMPLEMENTED | — | No | Base context becomes historical/stale; refresh deliberately deferred |
| JIT | NOT IMPLEMENTED | — | No | Later phase |
| Open Code Review | NOT IMPLEMENTED | — | No | Later phase |
| Remote MiniCPM | NOT IMPLEMENTED | — | No | Later phase |
| Tasks/Reviews/Archive UI | NOT IMPLEMENTED | — | No | API stabilization first |

**Current phase:** Phase 4B bounded GraphContext enrichment implemented; native load runtime verification remains pending.

**Phase 1C status:** IMPLEMENTED — NOT FULLY RUNTIME VERIFIED.

**Last successful runtime test:** real Graphify 0.9.67 indexed an A→B→C fixture and produced a 1,736-byte/579-token bounded briefing with 100% curated file/symbol recall and zero irrelevant files.

**Current blocker:** the current container has no running Phase-3 Plandex/PostgreSQL runtime or persisted authenticated CLI home, so native `load`/`ls` verification could not complete. Spark, GitHub PAT, and Needle remain independent external boundaries.

**Next recommended action:** rerun the native GraphContext `load --name` plus `ls --json` smoke in the prepared Phase-3 deployment; after it passes, begin Phase 5 JIT planning/checkpoints.

### Phase 3H rerun (2026-09-25)
Fresh PostgreSQL 16.15, freshly built native binaries, an empty dedicated database, a clean persistent CLI home, and the pinned tokenizer reconfirmed the existing `IMPLEMENTED + VERIFIED` Plandex statuses. Native first/repeat/recovery auth and `new`/`current --json` survived server restart with stable plan/project identity. The deployment tokenizer scripts were corrected to remain independent of Open WebUI application imports. Spark, GitHub authenticated reads, and Needle live inference remain `IMPLEMENTED — NOT RUNTIME VERIFIED`.

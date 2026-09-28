# AGENT.md

## Deployment & Agent Guidelines

This repository contains the Open WebUI Agent Platform, integrating Plandex, Graphify, Needle, and JIT Policy planning layers into a unified single-container deployment.

### Key Deployment Policies & Contracts

1. **Persistent Storage Root (`APP_PERSIST_ROOT`):**
   - The authoritative persistent volume root is `/data/agent-platform`.
   - Subdirectories mapped under this root:
     - PostgreSQL database: `/data/agent-platform/pgdata`
     - Plandex server state: `/data/agent-platform/plandex/server`
     - Plandex CLI home: `/data/agent-platform/plandex/cli`
     - Graphify index cache: `/data/agent-platform/graphify`
     - Control Plane state: `/data/agent-platform/control-plane`
     - Open WebUI data: `/data/agent-platform/open-webui`

2. **Network Port Binding Contract:**
   - `0.0.0.0:7860`: Open WebUI public interface (Hugging Face Space default port).
   - `127.0.0.1:8099`: Local Plandex server endpoint.
   - `127.0.0.1:8787`: Local Headroom context compressor.
   - `127.0.0.1:8790`: Local Spark contract adapter.
   - No other services or processes should bind directly to public `0.0.0.0` interfaces.

3. **JIT Policy & Execution Architecture:**
   - JIT acts as a long-horizon strategy and checkpoint layer. JIT does NOT directly execute commands, mutate worktrees, or replace Plandex tactical execution.
   - If JIT is unreachable, the runtime fails open to conservative local Plandex default policy without halting task execution.

4. **Testing & Pre-Commit Requirements:**
   - Always run `PYTHONPATH=backend:. .venv/bin/pytest -v` to verify local control plane, Graphify, Plandex, and JIT adapter tests before pushing changes.

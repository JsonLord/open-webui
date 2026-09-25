# Next steps

1. **Close the Phase 4B native runtime check.** In the prepared Phase-3 deployment, create a native plan, load one bounded structural brief with `plandex load --name`, and verify exactly one entry with `plandex ls --json`; no Spark inference is needed.
2. **Then begin Phase 5 — JIT long-horizon planning and bounded execution checkpoints.** Consume normalized task input and bounded GraphContext metadata without moving execution ownership out of Plandex.
3. **Keep post-edit graph refresh deferred.** Loaded evidence remains explicitly scoped to the base SHA and `graph_context_current` becomes false after edits.
4. **Keep external verification independent.** Credentialed Spark, GitHub PAT reads, and live Needle inference remain separate checks.

## After the Phase 3H rerun
The local native Plandex lifecycle is closed again on a fresh runtime. Continue with Phase 4B native Graphify context load/list verification. Treat the observed first-ever LiteLLM worker import taking slightly longer than the server's ten-second internal deadline as a supervisor warm-up/start-order consideration; do not redesign local auth.

# Current State
Version: 1.2.0

This reflects the verified code/runtime state for `ai-agent/` on branch `localstate` as checked on 2026-02-22 (UTC).

## Phase status (1-12)

1. `Phase 1 - Real patch generation`: Implemented  
   - LLM patch ops (`llm/patch_llm.py`)  
   - Local patch apply before test (`main.py` `_apply_patch_ops_locally`)  
   - Multi-file Git Data API tree commit (`github/git_api_commit.py` `build_tree_from_patchops`)
2. `Phase 2 - PR comment automation`: Implemented  
   - Structured test report body (`main.py` `_build_pr_test_comment`)  
   - Upsert comment by marker (`github/pr_manager.py` `upsert_pr_comment`)
3. `Phase 3 - AI Stop`: Implemented  
   - PR comment scan for stop phrase (`github/pr_manager.py` `has_ai_stop_comment`)  
   - Per-issue and global stop control state (`main.py` `_mark_ai_stopped`, `state["pr_controls"]`)
4. `Phase 4 - LLM stability`: Implemented  
   - 429/5xx backoff + retry (`llm/strategy_llm.py` `_post_with_backoff`)  
   - Relevant-error extraction (`core/test_runner.py` `_extract_relevant_error_lines`)  
   - Confidence-gated switching + strategy memory/quarantine (`core/test_runner.py`)
5. `Phase 5 - Safe autonomous mode`: Implemented  
   - Manual-approval gating (`main.py` `_has_manual_approval`)  
   - Branch iteration naming and PR auto-update controls (`main.py` `_resolve_branch_for_issue`)  
   - Policy-driven daily/weekly new-PR budgets (`policy.pr.max_per_day`, `policy.pr.max_per_week`)
6. `Phase 6 - Advanced features (selected)`: Partially implemented  
   - Optional LLM-generated test patch ops (`llm/patch_llm.py` `propose_test_patch_ops`)  
   - Ephemeral Docker Compose strategy (`core/test_runner.py` `strat_docker_compose_ephemeral_up`)  
   - GitHub Checks API publication (`github/checks_manager.py`)
7. `Phase 7 - Local LLM operationalization`: Implemented  
   - Provider health checks, fallback chain, request telemetry (`llm/provider.py`)
8. `Phase 8 - Service/API layer`: Implemented  
   - FastAPI control plane + managed worker threads (`service/api.py`)
9. `Phase 9 - Web UI MVP`: Implemented  
   - Dashboard served from API process (`service/static/index.html`, `service/api.py`)
10. `Phase 10 - Governance/safety expansion`: Implemented  
   - Repo policy files + resolver (`config/policies/*.json`, `core/policy.py`)  
   - Dry-run mode that executes locally but skips GitHub writes (`main.py`)  
   - Budget/accounting state (`state["daily_pr_counts"]`, `state["weekly_pr_counts"]`)
11. `Phase 11 - Observability + CI integration`: Implemented  
   - Structured event logging + metric registry (`core/observability.py`)  
   - Metrics/events/CI endpoints (`service/api.py`)  
   - CI-aware gating before iterative PR updates (`main.py`, `github/ci_status.py`)
12. `Phase 12 - Advanced autonomy improvements`: Implemented  
   - Strategy memory decay + quarantine (`core/test_runner.py`)  
   - Policy-driven autonomy knobs (`core/policy.py`, `config/policies/default.json`)  
   - Staged test-patch fallback with confidence gating (`main.py`)

## Revalidation evidence (2026-02-22 UTC)

1. Venv compile/import sanity passed:
   - `python -m compileall -q ai-agent`
   - `python -c "import main; import service.api"`
2. API smoke checks passed from current code:
   - `GET /health`
   - `GET /repos`
   - `GET /policies`
   - `GET /repo/KRT-leadtool/summary`
3. Policy loader resolved cleanly:
   - policy dir: `ai-agent/config/policies`
   - repo overrides: `KRT-leadtool`, `KRT-Com_Discord`
   - parse errors: none
4. LLM runtime behavior:
   - local endpoint `http://127.0.0.1:11434` currently unreachable in this VM
   - provider chain uses OpenAI as active when local is unhealthy
   - telemetry/log files located via `paths.LOGS_DIR`

## Operational snapshot

1. `state.json` (path via `paths.STATE_FILE`) tracks repos and runtime metadata (`llm_runtime`, `policy_runtime`, `strategy_memory`).
2. Branch `localstate` is in sync with origin after latest docs push.
3. No local Ollama container/process is running; OpenAI is active provider.
4. Paths are resolved via `ai-agent/paths.py` (no hardcoded `/home/codingai`).

## Known gaps after Phase 12

1. No authentication/authorization on the FastAPI control plane.
2. UI is polling-based only (no SSE/websocket streaming).
3. Runtime persistence is still JSON (`state.json`), not SQLite.
4. Repo list comes from `AVAILABLE_REPOS`/`TARGET_REPOS` env, not `config/repos.yaml`.
5. Research uses external web calls only when enabled; no local SearxNG yet.
6. Roadmap features (Phases 13–21) are planned but not yet implemented.

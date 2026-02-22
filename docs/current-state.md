# Current State

This reflects the code currently in `ai-agent/` on branch `localstate`.

## Phase status (1-11)

1. `Phase 1 - Real patch generation`: Implemented  
   - LLM patch ops (`llm/patch_llm.py`)  
   - Local patch apply before test (`main.py` `_apply_patch_ops_locally`)  
   - Multi-file Git Data API tree commit (`github/git_api_commit.py` `build_tree_from_patchops`)
2. `Phase 2 - PR comment automation`: Implemented  
   - Structured test report body (`main.py` `_build_pr_test_comment`)  
   - Upsert comment by marker (`github/pr_manager.py` `upsert_pr_comment`)
3. `Phase 3 - AI Stop`: Implemented  
   - PR comment scan for `AI Stop` (`github/pr_manager.py` `has_ai_stop_comment`)  
   - State lockout flags (`main.py` `_mark_ai_stopped`)
4. `Phase 4 - LLM stability`: Implemented  
   - 429/5xx backoff + retry (`llm/strategy_llm.py` `_post_with_backoff`)  
   - Error-line extraction (`core/test_runner.py` `_extract_relevant_error_lines`)  
   - Confidence-gated switching + strategy memory (`core/test_runner.py`)
5. `Phase 5 - Safe autonomous mode`: Implemented  
   - Daily PR cap (`main.py` `MAX_PRS_PER_REPO_PER_DAY`)  
   - Manual-approval gating (`main.py` `_has_manual_approval`)  
   - Iteration branch naming + PR auto-update controls (`main.py` `_resolve_branch_for_issue`)
6. `Phase 6 - Advanced features (selected)`: Partially implemented  
   - Optional LLM-generated test patch ops (`llm/patch_llm.py` `propose_test_patch_ops`)  
   - Ephemeral docker compose strategy (`core/test_runner.py` `strat_docker_compose_ephemeral_up`)  
   - GitHub Checks API publication (`github/checks_manager.py`)
7. `Phase 7 - Local LLM operationalization`: Implemented  
   - Provider health checks, fallback chain, telemetry counters/file (`llm/provider.py`)
8. `Phase 8 - Service/API layer`: Implemented  
   - FastAPI control plane + worker manager (`service/api.py`)
9. `Phase 9 - Web UI MVP`: Implemented  
   - Dashboard served from API process (`service/static/index.html`, `service/api.py`)
10. `Phase 10 - Governance/safety expansion`: Implemented  
   - Repo policy files + resolver (`config/policies/*.json`, `core/policy.py`)  
   - Daily and weekly PR budgets from policy (`main.py`)  
   - Dry-run mode that skips GitHub writes (`main.py`)
11. `Phase 11 - Observability + CI integration`: Implemented
   - Structured event logging + metric registry (`core/observability.py`)
   - Prometheus-style metrics + event/CI endpoints (`service/api.py`)
   - CI-aware gating before iterative PR updates (`main.py`, `github/ci_status.py`)

## Runtime behavior summary

1. Poll `ai-fix` issues.
2. Resolve safe branch/PR state and stop/manual-approval gates.
3. Generate patch ops with LLM.
4. Apply patch locally, run adaptive tests on patched content.
5. Commit to GitHub only when tests pass.
6. Upsert PR report comment and optional check-run.
7. Persist operational state in local `state.json`.
8. Apply per-repo governance policy for approvals, budgets, and dry-run execution.

## Known gaps after Phase 11

1. No authentication/authorization on FastAPI control plane.
2. UI is polling-based only (no websocket streaming).
3. No dedicated persistence backend beyond local `state.json`.
4. `config/repos.yaml` is not currently the authoritative repo source (runtime uses `AVAILABLE_REPOS`/`TARGET_REPOS` plus policy files).
5. Optional advanced goals from original Phase 6 remain open (for example deeper multi-repo orchestration and self-modifying strategy logic).

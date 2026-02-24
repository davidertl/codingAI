# Current State
Version: experimental-0.23.0

This reflects verified code/runtime state for `ai-agent/` on branch `localstate` as checked on 2025-06-25 (UTC).

## Phase status (1-22)

1. `Phase 1 - Real patch generation`: Implemented
2. `Phase 2 - PR comment automation`: Implemented
3. `Phase 3 - AI Stop`: Implemented
4. `Phase 4 - LLM stability`: Implemented
5. `Phase 5 - Safe autonomous mode`: Implemented
6. `Phase 6 - Advanced testing features`: Partially implemented
7. `Phase 7 - Local LLM operationalization`: Implemented
8. `Phase 8 - API control plane`: Implemented
9. `Phase 9 - Web UI MVP`: Implemented
10. `Phase 10 - Governance/safety expansion`: Implemented
11. `Phase 11 - Observability + CI integration`: Implemented
12. `Advanced autonomy improvements`: Implemented
13. `Phase 13 - Setup & installer`: Implemented
14. `Phase 14 - Projects (repo discovery)`: Implemented
15. `Phase 15 - Chat + Repo UX`: Implemented (DB-backed threads/messages, true SSE streaming, file tree/snippet, chat UI)
16. `Phase 16 - Issue -> Patch -> Diff -> Pause -> Test -> PR`: Implemented
17. `Phase 17 - Containerized execution / job worktrees`: Implemented
18. `Phase 18 - Research (local-first)`: Implemented (runtime depends on reachable Searx endpoint)
19. `Phase 19 - Memory & reuse`: Implemented
20. `Phase 20 - Secrets & safety (local)`: Implemented (prompt-safety filter, secret denylist, XSS hardening, setup audit)
21. `Phase 21 - Autonomous mode & self-tasks`: Implemented (self-task scanner, repeated-failure detection, deduped issue creation, UI)
22. `Phase 22 - Deterministic orchestration V2`: Implemented (contracts/roles/engine/API; V2 is now the PRIMARY pipeline, V1 retained as fallback)

## 2026-02-23 revalidation highlights

1. API health/setup:
   - `GET /health` returns `setup_required` and embedded setup status.
   - `GET /setup/status` and `GET /setup/values` both return expected data.
2. Setup UX:
   - Setup form is prefilled from saved `.env` values via `/setup/values`.
   - PEM upload supports overwrite policy (`SETUP_PEM_OVERWRITE`) and writes setup audit events.
3. Projects:
   - Per-repo enable/disable semantics fixed for first explicit toggle from implicit-all mode.
4. GitHub integration config:
   - Hardcoded owner usage removed from GitHub modules; owner resolves dynamically from setup/env.
5. Web strategy:
   - New `web_live_playwright` strategy supports deterministic CodingAI dashboard checks and passed:
     - Run Once
     - Start Worker / Stop Worker
     - Refresh
     - Project enable/disable/enable
     - Automation enable/disable
6. Orchestrator V2:
   - Added strict contracts + policy validators under `ai-agent/orchestrator/`.
   - Added role modules (`ingest`, `planner`, `researcher`, `coder`, `reviewer`, `test_interpreter`, `judge`).
   - Added run persistence (`orchestrator_runs`, `orchestrator_attempts`, `orchestrator_artifacts`) in `state.db`.
   - Added orchestration APIs:
     - `GET /orchestration/runs`
     - `GET /orchestration/runs/{run_id}`
     - `GET /orchestration/runs/{run_id}/artifacts`
   - Added sandbox execution boundary adapter (`core/sandbox_runner_client.py`) with `RUNNER_MODE=sandbox_api`.

## Evidence snapshot

1. Compile/import sanity:
   - `python -m py_compile` passed for updated API/test-runner/GitHub modules.
2. Metrics/events:
   - `GET /metrics` and `GET /events` return structured observability data.
3. Governance/policy:
   - `GET /policies` and per-repo summaries load with no policy parse errors.
4. Research:
   - `GET /research` endpoint works; in this VM local Searx upstream at `127.0.0.1:8080` is currently unavailable, so search reports upstream connection error instead of results.

## Known open gaps

1. API authentication/authorization skipped (single-user behind reverse proxy).
2. Full E2E test suite (Playwright smoke tests) not yet implemented.
3. Dependency-drift scheduled checks not yet implemented.

## 0.23.0 changes

1. **Critical fix**: `CODINGAI_DB_FILE` now defaults to `/app/ai-agent/db/state.db` (inside `codingai-db` Docker volume). Previously the DB was in the ephemeral container layer.
2. **Critical fix**: `SEARX_URL` env var name in docker-compose now matches `research.py` (was `SEARXNG_BASE_URL`).
3. **Chat streaming**: Migrated from broken SSE to WebSocket (`/ws/live` with `chat_send` message type). Fallback to REST POST if WebSocket is disconnected.
4. **Request timeouts**: All `github/*.py` modules now use `timeout=30` on every `requests.*()` call.
5. **Orchestration Runs UI**: New collapsible panel in dashboard, populated via WebSocket snapshots, with run detail drill-down.
6. **Research UI**: New search panel with query input, results display, and summary from SearxNG.
7. **File Browser UI**: Chat section now has a file browser button to browse repo trees and attach file snippets.
8. **Researcher role wired**: `build_research_brief` now executes between planner and coder in V2 orchestration engine.
9. **Labels API**: `PUT /projects/{repo}/labels` + UI per-project labels editing.
10. **Dead code cleanup**: Removed unused `config/repos.yaml`.
11. **Docs**: All doc version headers synced to 0.23.0. Updated phase8 API catalog, operations runbook (state.db volume), security report (SBP-007/008 marked fixed).

Implementation plan for all open roadmap items: `docs/implementation-plan.md`.

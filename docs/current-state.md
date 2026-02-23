# Current State
Version: experimental-0.22.0

This reflects verified code/runtime state for `ai-agent/` on branch `localstate` as checked on 2026-02-23 (UTC).

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
15. `Phase 15 - Chat + Repo UX`: Pending
16. `Phase 16 - Issue -> Patch -> Diff -> Pause -> Test -> PR`: Implemented
17. `Phase 17 - Containerized execution / job worktrees`: Implemented
18. `Phase 18 - Research (local-first)`: Implemented (runtime depends on reachable Searx endpoint)
19. `Phase 19 - Memory & reuse`: Implemented
20. `Phase 20 - Secrets & safety (local)`: Partially implemented
21. `Phase 21 - Autonomous mode & self-tasks`: Partially implemented
22. `Phase 22 - Deterministic orchestration V2`: Partially implemented (contracts/roles/engine/API implemented; currently wired as preflight gate via feature flag)

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

1. API authentication/authorization remains optional and currently not enabled.
2. UI is polling-based (no SSE/websocket stream for updates yet).
3. Persistence is still JSON (`state.json`) instead of SQLite.
4. Phase 15 (chat/repo UX) is pending.
5. Phase 20 prompt-safety filter is not yet implemented.
6. Phase 21 missing-feature self-task issue creation is not yet implemented.
7. Full V2 replacement mode is not yet active by default; currently `ORCHESTRATOR_V2_ENABLED` runs as preflight before legacy patch/test flow.

Implementation plan for all open roadmap items: `docs/implementation-plan.md`.

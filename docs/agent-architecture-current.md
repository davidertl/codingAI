# Agent Architecture (Current)
Version: experimental-0.22.0

## Core pipeline (current)

- `ai-agent/main.py` orchestrates per-repo cycles:
  1) Ensure LLM ready (`llm/provider.py`), load policy (`core/policy.py`).
  2) Fetch issues labeled `ai-fix` (GitHub App).
  3) Gates: AI Stop, manual approval, cooldown, PR caps, CI gate (iterative updates).
  4) Resolve iteration branch name; generate patch ops (`llm/patch_llm.py`).
  5) Apply patch locally; run adaptive tests (`core/test_runner.py`), optional test-patch fallback.
  6) If tests pass and not dry-run: Git Data API commit/branch update (`github/git_api_commit.py`), PR create/reuse, report comment + optional check-run.

## Module map (current)

- `paths.py`: resolves repo root, logs, state, env, pem, workspaces; removes hardcoded `/home`; exposes `setup_status()` and central PEM/env paths for setup UI.
- `core/policy.py`: env + policy files merge/normalize; per-repo overrides.
- `core/test_runner.py`: repo analysis; docker/dotnet/node strategies; LLM-guided strategy switching; strategy memory/quarantine; error extraction.
- `core/research.py`: SearxNG-backed search with TTL cache and optional LLM summary.
- `strategy_error_memory`: persisted mapping from error fingerprint → preferred strategy to bias future runs.
- `core/observability.py`: counters/gauges/summaries; JSONL events; `/metrics`, `/events`; redaction for sensitive keys in event payloads.
- `llm/provider.py`: provider chain (openai/local/auto), health checks, failover, telemetry; resolves env from `paths.ENV_FILE`.
- `llm/patch_llm.py`: patch + optional test-patch generation with schema validation.
- `llm/strategy_llm.py`: next-strategy selector with retry/backoff.
- `github/app_auth.py`: GitHub App JWT + installation token; PEM path via `paths.GITHUB_APP_PEM_FILE`.
- `github/git_api_commit.py`: blob/tree/commit/ref without git push.
- `github/issue_manager.py`: issue polling and failure comment upsert on source issues; optional follow-up issue creation after repeated failures.
- `github/pr_manager.py`: PR create/reuse, AI Stop detection, comment upsert.
- `github/checks_manager.py`: GitHub Checks API publish.
- `github/ci_status.py`: combined status + check-runs for CI gate.
- `github/repo_manager.py`: shared mirrors under `workspaces/repos`, per-job worktrees under `workspaces/jobs/{repo}/{job_id}`, TTL cleanup; lists installation repos for projects UI.
- `service/api.py`: FastAPI control plane, workers, metrics/events/CI endpoints, setup endpoints (`/setup/status`, `/setup/github`, `/setup/pem`), projects enable/disable (`/projects*`), automation toggles (`/automation/*`), pipeline controls (`/pipeline/{repo}/{issue}/pause|resume|cancel|diff`), serves dashboard (`service/static/index.html`).
- `service/static/index.html`: Dashboard wiring for setup status/PEM upload, installation repo list with enable/disable, worker controls, queue/tracked issue views, pipeline diff/pause/resume/cancel buttons per tracked issue.
- `docker-compose.yaml`: binds `.env` and `github_app/` into the service container read-write to support setup UI writes.

## State model (runtime, JSON)

- File: `state.json` (path from `paths.STATE_FILE`), not in git.
- Per issue (`state[repo][issue]`): `active_branch`, `pr_created`, `pr_number/url`, `last_status`, `last_error`, `retry_after`, `patch_ops_count/confidence`, `report_comment_id`, `check_run_url`, `pending_manual_approval`, `ai_stopped*`, `test_patch_*`, strategy thresholds, durations.
- Global: `daily_pr_counts`, `weekly_pr_counts`, `strategy_memory`, `pr_controls`, `llm_runtime`, `policy_runtime`.

## Safety properties (current)

- Uses GitHub Git Data API (no git push/force); branch created only after tests pass.
- AI Stop via PR comments halts processing.
- Manual approval/dry-run/policy caps enforced before writes.
- CI gate can block iterative PR updates.
- Patch/test failures keep changes local (no remote writes).

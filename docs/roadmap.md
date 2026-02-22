# CodingAI Roadmap (Phases 21 → 1, Local-Only VM)

## Phase 21 – Autonomous Mode & Self-Tasks
- Per-project “full automation” toggle (default off) honoring push gate.
- Agent can open GitHub issues in `davidertl/codingAI` for missing features/bugs.
- Scheduled self-checks: dependency drift, disk space, workspace cleanup.

## Phase 20 – Secrets & Safety (Local)
- PEM/API key upload rules: size/type validation, overwrite policy, fingerprint stored, audit entry; never echo secret content.
- Redaction for logs/metrics; lightweight prompt-safety filter even in local mode.

## Phase 19 – Memory & Reuse
- Strategy memory/quarantine persisted (to move to SQLite); error fingerprints map to successful strategy chains.
- UI surfaces “similar failures”; strategies auto-biased by history.

## Phase 18 – Research (Local-First)
- SearxNG service; research cache (query/results/summary TTL).
- Summaries via local LLM; optional API fallback toggle.

## Phase 17 – Containerized Execution
- Layout: `repos/` mirrors, `jobs/<id>/` worktrees with TTL cleanup.
- Docker via host socket (trusted single-user); optional cgroup caps/rootless.
- Test runner keeps existing strategies; optional deploy+smoke step; DAU/pentest flag off by default.

## Phase 16 – Issue → Patch → Diff → Pause → Test → PR
- Job state: queued → analyzing → patching → diff_ready → pause_window (10s) → testing → passed → pushing → pr_created | failed_no_push | needs_user_input | canceled.
- Diff viewer + pause/cancel controls; logs/test output visible.
- Failures keep local worktree/branch (unpushed); actions: retry, delete, promote. Pass: commit/push side branch + PR.

## Phase 15 – Chat + Repo UX
- Chats/messages in DB; repo-scoped threads; SSE streaming.
- File tree browse (read-only) and attach snippets.
- Model routing per task type; UI toggles “use API for review/planning/research”; local LLM default.

## Phase 14 – Projects (Repo Discovery)
- List GitHub App installation repos; enable/disable per repo.
- Persist projects: push gate mode (10s auto vs manual approve), labels to watch, policy reference.

## Phase 13 – Setup & Installer (Local)
- Docker Compose stack: api/ui, optional searxng, sqlite volume, secrets volume, workspaces.
- Installer: creates `codingai` user, installs Docker/Compose, starts stack on configurable port (default 80/8000).
- Setup UI: GitHub owner/App/Installation IDs, PEM upload (validate, store 0600, fingerprint only), optional OpenAI key, local LLM URL/mode/model.
- Health shows `setup_required` until pem+IDs validate.

## Phase 12 – Advanced Autonomy Improvements
- Strategy memory decay/quarantine; staged test-patch fallback with confidence gating; policy knobs for autonomy.

## Phase 11 – Observability and CI Integration
- Structured events (JSONL), counters/gauges/summaries; `/metrics`, `/metrics/json`, `/events`.
- CI gate via `ci_status`: require_green_before_update, block_on_pending/failed, allowed conclusions, on_error, retry_after.
- `GET /ci/{repo}/{pr_number}`; CI gate state surfaced in `/repo/{repo}/summary`.

## Phase 10 – Governance and Safety Expansion
- Policy files `ai-agent/config/policies/{default,repo}.json`; env overrides.
- Sections: enabled, dry_run, safety, approval, pr, patch, strategy, branch.
- Budgets: daily/weekly PR caps; counters in state; dry-run skips GitHub writes.
- API: `/policies`, `/policy/{repo}`, policy/budget in `/repo/{repo}/summary`.

## Phase 9 – Web UI MVP
- Dashboard served from API; repo controls (start/stop/run once), queue, tracked issues/PR/test signals.

## Phase 8 – API Control Plane
- FastAPI control plane; worker manager; endpoints for health, repos, state, policies, metrics/events, CI status, run/stop/run-once.

## Phase 7 – Local LLM Operationalization
- Provider health checks, fallback chain, telemetry; supports local+OpenAI with fallback.

## Phase 6 – Advanced Testing Features
- Optional LLM-generated test patch ops; Docker Compose ephemeral up; GitHub Checks API publishing.

## Phase 5 – Safe Autonomous Mode
- Manual approval gating; branch iteration naming; PR caps; dry-run mode.

## Phase 4 – LLM Stability
- 429/5xx backoff; relevant error extraction; confidence-gated strategy switching; strategy memory basics.

## Phase 3 – AI Stop
- Detect “AI Stop” in PR comments; state lockout per issue/PR; skip processing.

## Phase 2 – PR Comment Automation
- Structured test report comment with marker upsert (create/update).

## Phase 1 – Real Patch Generation
- LLM patch ops schema; local apply before tests; Git Data API multi-file commit/branch update.

## Technology Choices (Local VM)
- SQLite WAL: zero admin, future-ready for Postgres migration.
- Local LLM: Ollama by default; OpenAI-compatible local server optional; API models opt-in per task.
- Research: self-hosted SearxNG (no per-query cost) with optional API fallback.
- Docker: host `docker.sock` acceptable in trusted single-user VM; simpler/faster than DinD.

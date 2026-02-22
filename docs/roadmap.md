# CodingAI Roadmap (Phases 1–21, Local-Only VM)

## Baseline (Phases 1–12)
- Phase 1: Structured patch ops (LLM), local apply, Git Data API multi-file commit.
- Phase 2: PR comment automation with marker upsert.
- Phase 3: AI Stop via PR comments; state lockout.
- Phase 4: LLM stability (429/5xx backoff, error trimming, confidence-gated strategy switching).
- Phase 5: Safe autonomy (manual approval, branch iteration, PR caps, dry-run).
- Phase 6: Advanced tests (optional test-patch ops, compose ephemeral runs, Checks API).
- Phase 7: Local LLM operationalization (health checks, fallback, telemetry).
- Phase 8: FastAPI control plane + worker manager.
- Phase 9: Web UI dashboard (issues/PR/test signals).
- Phase 10: Governance/budgets/dry-run
  - Policy files `ai-agent/config/policies/{default,repo}.json`; env overrides.
  - Sections: enabled/dry_run/safety/approval/pr/patch/strategy/branch.
  - Daily/weekly PR caps (`state.json` counts); dry-run skips all GitHub writes.
  - API: `/policies`, `/policy/{repo}`, `/repo/{repo}/summary` shows policy/budget.
- Phase 11: Observability + CI gate
  - Event log JSONL; counters/gauges/summaries; `/metrics`, `/metrics/json`, `/events`.
  - CI gate via `ci_status`: require_green_before_update, block_on_pending/failed, allowed conclusions, on_error.
  - API: `/ci/{repo}/{pr_number}`, summaries in `/repo/{repo}/summary`.
- Phase 12: Autonomy improvements (strategy memory decay/quarantine, staged test-patch fallback, policy knobs).

## Phase 13 – Setup & Installer (Local)
- Docker Compose stack: api/ui, searxng (optional), sqlite volume, secrets volume, workspaces.
- Installer: creates `codingai` user, installs Docker/Compose, starts stack on configurable port (default 80/8000).
- Setup UI: GitHub owner/App/Installation IDs, PEM upload (validate, store 0600, fingerprint only), optional OpenAI key, local LLM URL/mode/model.
- Health shows `setup_required` until pem+IDs validate.

## Phase 14 – Projects (Repo Discovery)
- List repos from GitHub App installation; enable/disable per repo in UI.
- Persist projects in SQLite: push gate mode (10s auto vs manual approve), labels to watch, policy reference.

## Phase 15 – Chat + Repo UX
- Chats/messages in SQLite; repo-scoped threads; SSE streaming.
- File tree browse (read-only) and attach snippets.
- Model routing per task type; UI toggles “use API for review/planning/research”; local LLM default.

## Phase 16 – Issue → Patch → Diff → Pause → Test → PR
- Job state: queued → analyzing → patching → diff_ready → pause_window (10s) → testing → passed → pushing → pr_created | failed_no_push | needs_user_input | canceled.
- Diff viewer + pause/cancel controls; logs/test output visible.
- Failures keep local worktree/branch (unpushed); actions: retry, delete, promote. Pass: commit/push side branch + PR.

## Phase 17 – Containerized Execution
- Layout: `repos/` mirrors, `jobs/<id>/` worktrees with TTL cleanup.
- Docker via host socket (trusted single-user); optional cgroup caps/rootless.
- Test runner keeps existing strategies; optional deploy+smoke step; DAU/pentest flag off by default.

## Phase 18 – Research (Local-First)
- SearxNG in compose; research cache in SQLite (query/results/summary TTL).
- Summaries via local LLM; optional API fallback toggle.

## Phase 19 – Memory & Reuse
- Strategy memory/quarantine in SQLite.
- Map error fingerprints to successful strategy chains; surface “similar failures” in UI; auto-bias strategies.

## Phase 20 – Secrets & Safety (Local)
- PEM/API key upload rules: size/type checks, overwrite policy, fingerprint stored, audit log entry; never echo secret content.
- Redaction for logs/metrics; lightweight prompt-safety filter even in local mode.

## Phase 21 – Autonomous Mode & Self-Tasks
- Per-project “full automation” toggle (default off) that honors current push gate.
- Agent can file GitHub issues in `davidertl/codingAI` describing missing features/bugs.
- Scheduled self-checks: dependency drift, disk space, workspace cleanup.

## Technology Choices (Why)
- SQLite WAL: zero admin, fast enough for single-user; versioned schema for future Postgres.
- Local LLM: Ollama by default; OpenAI-compatible local server optional; API models opt-in per task.
- Research: self-hosted SearxNG (no query cost) with optional API fallback.
- Docker: host `docker.sock` acceptable in trusted single-user VM; simpler/faster than DinD.

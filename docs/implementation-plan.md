# CodingAI Implementation Plan (Docs ↔ Code Gaps)
Version: experimental-0.21.0
Last updated: 2026-02-23 (UTC)

This plan tracks roadmap features that are documented but missing/partial on `localstate` (plus a few security-critical prerequisites).

## Docs provenance (what introduced the missing features)

Roadmap bullets for Phase 13–15 and Phase 20–21 were added in `bc37cd4` (“docs: expand roadmap…”).  
The “Pre‑Phase 15 checklist” was added in `f834a65` (“Phase14 setup UI wiring, projects toggle…”).

## Gap summary (branch `localstate` @ `1d85a00`)

1. Phase 15 (Chat + Repo UX) is not implemented (DB-backed threads/messages, SSE streaming, file tree/snippet attach, task-type routing, UI).
2. Phase 14 “Projects” is only partially implemented: enable/disable doesn’t gate worker execution and isn’t durable (still `state.json`), and per-repo labels/push-gate/policy ref are not implemented.
3. Phase 13 “Compose stack + setup UX” is incomplete: compose lacks optional SearxNG/sqlite/secrets volumes; UI setup lacks OpenAI/local-LLM controls.
4. Phase 20 is incomplete: prompt-safety filter and setup audit trail are missing; `.env` is currently eligible for inclusion in LLM repo-context collection.
5. Phase 21 is incomplete: issue creation for missing features/bugs is not wired; dependency-drift checks are not implemented.
6. Cross-cutting: control plane has no auth; dashboard uses `innerHTML` with untrusted content (XSS risk); GitHub owner is hardcoded in some modules; repo config file (`ai-agent/config/repos.yaml`) is not used.

## Recommended implementation order

### 0) Security foundation (do this before expanding UI/features)

1. Add optional authn/authz for the control plane (admin vs read-only).
2. Fix dashboard DOM XSS (remove/avoid `innerHTML` for untrusted values or escape everywhere).
3. Validate repo identifiers everywhere they become filesystem paths; enforce path containment for any file browser.
4. Remove `.env` from any “repo context” collection for LLM prompts; add explicit allow/deny lists for sensitive files.

Acceptance checks:
- With `CODINGAI_ADMIN_TOKEN` set, anonymous `POST` calls return 401/403.
- A GitHub issue title like `<img src=x onerror=alert(1)>` renders as text (no execution).

### 1) Phase 13 follow-ups (installer + compose + setup UX)

**Compose**
1. Expand `docker-compose.yaml` to include:
   - `searxng` as an optional profile (or separate compose file), with a default `SEARX_URL` wiring.
   - A durable sqlite volume for `state.db` (or a host bind mount with enforced `0600`).
   - A secrets volume for `github_app/*.pem` (mounted read-only except during setup).
2. Ensure `workspaces/` is a durable volume/bind mount and is owned by a non-root runtime user.

**Installer**
1. Extend `scripts/install_codingai.sh` to:
   - Prompt for `GITHUB_OWNER` (and write it to `.env`).
   - Offer a “compose install mode” that starts the stack and prints the local URL(s).
   - Install a local LLM runtime option (native Ollama install on Debian, or Docker-based if preferred).

**Setup UI**
1. Add `/setup/values` (safe env read) and use it to prefill the UI (owner/app/installation ID; never return secrets).
2. Add setup forms for local LLM settings + optional OpenAI key (write to `.env` with strict validation + audit event).
3. Add `setup_required` into `/health` (or an explicit “setup readiness” object).

Acceptance checks:
- Fresh install + restart preserves `.env` + pem + DB without manual copying.
- `/setup/values` never includes secret material (PEM contents, API keys).

### 2) Phase 14 completion (Projects: discovery + durable config + gating)

1. Introduce SQLite-backed `projects` table:
   - `repo` (PK), `enabled`, `labels_json`, `push_gate_mode`, `policy_ref`, `updated_at`.
2. Migrate `/projects` enable/disable endpoints to use SQLite and include `labels` + `push_gate_mode` in payloads.
3. Make project enable/disable actually gate:
   - `main.get_available_repos()` (what workers can start for).
   - queue listing and automation scheduler behavior.
4. Replace hardcoded GitHub owner constants with `GITHUB_OWNER` everywhere (issues, repos, PRs, checks).
5. Use `labels_json` per repo to drive `get_ai_issues` (default `["ai-fix"]`).
6. Use `push_gate_mode` to override effective approval behavior (auto vs manual) without requiring an agent restart.

Acceptance checks:
- Disabled projects never start workers and never get processed in automation mode.
- Labels-per-repo changes affect the next queue poll.

### 3) Phase 15 (Chat + Repo UX)

Backend (SQLite + API):
1. Add tables for `threads`, `messages`, `attachments` (repo-scoped threads).
2. Add CRUD endpoints for threads and message creation.
3. Add SSE streaming for assistant output (reconnect-safe, message status transitions).
4. Add read-only file tree + snippet attach endpoints (strict path validation + size limits).
5. Implement model routing per task type (`review`, `planning`, `research`, `chat`) and per-task provider policy (“use API for X” toggles).

Frontend (UI):
1. Add chat panel per selected repo:
   - thread list, message list, composer, attachments.
2. Add toggles for “use API” per task type (persist in DB/state and reflect in effective policy).
3. Add file browser + “attach snippet” flow.

Acceptance checks:
- Repo-scoped chat persists across restarts.
- SSE continues after refresh/reconnect without duplicating content.
- Snippet attachments are included in the prompt context and are visible in message metadata.

### 4) Phase 18 (Research: local provisioning + health)

1. Add optional compose profile for SearxNG.
2. Add `/research/health` endpoint (checks `SEARX_URL`, cache readability, summary LLM readiness).
3. Surface research status in UI (green/yellow/red with last error).

Acceptance checks:
- After enabling the profile, `/research` returns results with `search_error=""`.

### 5) Phase 20 completion (Secrets & safety hardening)

1. Prompt-safety filter:
   - Block common exfiltration/secret-retrieval/system‑prompt dump patterns deterministically.
   - Enforce filter at all LLM call sites (strategy/patch/research summary/chat).
2. Setup audit trail:
   - Emit redacted events on setup changes (GitHub IDs changed, PEM replaced, OpenAI key set/cleared, LLM settings changed).
   - Add `/setup/audit` endpoint for recent setup events.
3. Tighten setup write paths:
   - Prevent newline/control-char injection into `.env`.
   - PEM overwrite policy + fingerprint storage (never echo secret contents).

Acceptance checks:
- Known bad prompts are blocked with a stable reason code + audit event.
- Setup actions show up in `/setup/audit` without secrets.

### 6) Phase 21 completion (Self-tasks + issue creation)

1. Add missing-feature / repeated-failure detector:
   - repeated strategy failures per fingerprint
   - unhealthy dependencies (optional)
   - disk pressure / workspace bloat
2. Implement deduped issue creation into `your-org/codingAI` (opt-in + rate-limited).
3. Add `/self-tasks` endpoint + UI list with cooldown and “snooze” controls.

Acceptance checks:
- Identical fingerprint opens at most one issue per cooldown window.

## Testing / verification strategy

1. Add unit tests for SQLite stores (projects + chat) and for prompt-safety filter behavior.
2. Add Playwright E2E smoke tests for the dashboard (either via the existing `core/test_runner` strategy or a standalone script):
   - setup form write/read
   - projects enable/disable + push gate selector
   - chat send + SSE stream
   - file browse + attach snippet
3. Add a “local-only” validation mode: bind to `127.0.0.1` and use a dummy GitHub API client for tests.

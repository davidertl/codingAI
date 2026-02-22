# CodingAI Roadmap (Post Phase 6)

## Current baseline

- Phases 1-6 core features are implemented in the agent.
- Local LLM provider mode is now implemented (`LLM_PROVIDER=local`) with OpenAI-compatible local endpoint support.
- Web UI is not implemented yet.

## Phase 7 - Local LLM Operationalization

Goal: make local-model execution production-ready.

Scope:

1. Add startup health-check for selected LLM provider.
2. Add explicit provider fallback chain (`local -> openai` optional flag).
3. Add model capability guardrails (max tokens/context limits per provider).
4. Add structured telemetry for provider latency/error rate.

Acceptance:

- Agent runs end-to-end with `LLM_PROVIDER=local` and no `OPENAI_API_KEY`.
- Failure mode is deterministic and visible in logs/state when local endpoint is unavailable.

## Phase 8 - Service/API Layer

Goal: expose agent control and observability via HTTP API.

Scope:

1. Introduce `FastAPI` service wrapping agent loop controls.
2. Endpoints:
   - `GET /health`
   - `GET /repos`
   - `GET /state`
   - `POST /run/repo/{repo}`
   - `POST /stop/repo/{repo}`
3. Move loop execution to managed background workers per repo.

Acceptance:

- Single process can be controlled without terminal interaction.
- State and last-run results are queryable over API.

## Phase 9 - Web UI MVP

Goal: operator dashboard for CodingAI.

Scope:

1. Build frontend (React + Vite) consuming Phase 8 API.
2. Views:
   - repo/issue queue
   - run status timeline
   - PR/test report panel
   - controls (start/stop/retry/AI Stop override)
3. Add live updates (polling first, websocket second iteration).

Acceptance:

- Operator can run agent without shell.
- Operator can inspect issue pipeline and PR outputs from browser.

## Phase 10 - Governance & Safety Expansion

Goal: tighten autonomous controls for real repos.

Scope:

1. Configurable per-repo policy files (limits, approval requirements, branch naming).
2. Daily/weekly budget controls (PR count, compute/time budget).
3. Dry-run mode with full simulation and no GitHub writes.

Acceptance:

- Risk controls are repo-specific and enforced uniformly.

## Phase 11 - Observability & CI Integration

Goal: make operations measurable and merge-safe.

Scope:

1. Structured logs + metrics export (JSON logs, Prometheus-style counters).
2. GitHub Checks enrichment for both pass and fail paths.
3. Optional merge-gate evaluation from CI/check status before follow-up actions.

Acceptance:

- Failures and bottlenecks are measurable.
- PR lifecycle decisions can include CI state.

## Phase 12 - Advanced Autonomy

Goal: controlled intelligence improvements.

Scope:

1. Strategy memory persistence with decay/scoring improvements.
2. Self-improving strategy suggestion workflow (guarded and reviewable).
3. Optional staged unit-test generation with confidence gating.

Acceptance:

- Strategy selection quality improves over time with bounded risk.

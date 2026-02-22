# CodingAI Roadmap (Post Phase 12)

## Current baseline

1. Phase 1-12 features are implemented in code (patch pipeline, PR reporting, AI Stop, LLM hardening, policy governance, budgets, dry-run, observability, CI gate integration, advanced autonomy tuning, API, Web UI).
2. Runtime is still single-node and file-state based (`state.json`).
3. Control plane/UI currently assume trusted network access.

## Phase 13 - Platform hardening + UX

Goal: prepare for broader usage.

Scope:

1. Add API authN/authZ and optional multi-user sessions.
2. Web UI improvements: live updates (websocket/SSE), filter/search, run history.
3. Replace local JSON persistence with durable store (SQLite/Postgres).

Acceptance:

1. Control plane is safe to expose behind standard internal ingress.
2. Operator experience supports multi-repo daily operation.

## Phase 14 - Durable state and data model upgrades

Goal: reduce operational risk from single-file state persistence.

Scope:

1. Move runtime state from `state.json` to SQLite/Postgres abstraction.
2. Add migration path and schema versioning.
3. Keep API compatibility for dashboard/state endpoints.

Acceptance:

1. Restarts are resilient and state is queryable without file parsing.
2. Multi-process safety improves over JSON-file writes.

## Phase 15 - Multi-repo orchestration and quality loops

Goal: evolve from single-node control to scalable autonomous orchestration.

Scope:

1. Multi-repo scheduler with weighted priorities and fairness.
2. Optional staged test-generation/review loop with safety guards.
3. Strategy quality feedback loop with confidence calibration over time.

Acceptance:

1. The system can process multiple repos predictably under policy constraints.
2. Strategy effectiveness improves with measurable, auditable history.

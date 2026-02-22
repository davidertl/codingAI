# CodingAI Roadmap (Post Phase 10)

## Current baseline

1. Phase 1-10 features are implemented in code (patch pipeline, PR reporting, AI Stop, LLM hardening, policy governance, budgets, dry-run, API, Web UI).
2. Runtime is still single-node and file-state based (`state.json`).
3. Control plane/UI currently assume trusted network access.

## Phase 11 - Observability and CI coupling

Goal: provide measurable reliability and stronger merge safety.

Scope:

1. Structured event log schema for all major pipeline steps.
2. Prometheus-style metrics endpoint for API service.
3. CI/check-state aware follow-up logic before iterative updates.

Acceptance:

1. Error rates and latency are queryable over time.
2. PR update decisions can include upstream CI status.

## Phase 12 - Platform hardening + UX

Goal: prepare for broader usage.

Scope:

1. Add API authN/authZ and optional multi-user sessions.
2. Web UI improvements: live updates (websocket/SSE), filter/search, run history.
3. Replace local JSON persistence with durable store (SQLite/Postgres).

Acceptance:

1. Control plane is safe to expose behind standard internal ingress.
2. Operator experience supports multi-repo daily operation.

## Phase 13 - Multi-repo orchestration and quality loops

Goal: evolve from single-node control to scalable autonomous orchestration.

Scope:

1. Multi-repo scheduler with weighted priorities and fairness.
2. Optional staged test-generation/review loop with safety guards.
3. Strategy quality feedback loop with confidence calibration over time.

Acceptance:

1. The system can process multiple repos predictably under policy constraints.
2. Strategy effectiveness improves with measurable, auditable history.

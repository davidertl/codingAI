# CodingAI Docs

Last updated: 2026-02-22  
Branch baseline: `localstate`

This folder documents the current implementation state of CodingAI (Phases 1-12 complete in code), how to run it, and what comes next.

## Start here

1. `current-state.md` for phase completion status and feature matrix.
2. `operations-runbook.md` for install/run/troubleshooting.
3. `phase8-api-control-plane.md`, `phase9-web-ui-mvp.md`, `phase10-governance-safety.md`, `phase11-observability-ci.md`, and `phase12-advanced-autonomy.md`.

## Index

- `current-state.md`: implementation status by phase (1-12), plus known gaps.
- `agent-architecture-current.md`: end-to-end control flow and module map.
- `strategy-mapping.md`: adaptive test strategy detection/selection behavior.
- `operations-runbook.md`: day-2 operations and common procedures.
- `local-llm-setup.md`: local model setup/fallback/telemetry configuration.
- `phase8-api-control-plane.md`: FastAPI control plane endpoints and examples.
- `phase9-web-ui-mvp.md`: dashboard usage and supported controls.
- `phase10-governance-safety.md`: policy files, budgets, and dry-run behavior.
- `phase11-observability-ci.md`: structured events, metrics, and CI gate integration.
- `phase12-advanced-autonomy.md`: strategy-memory autonomy and staged test-patch fallback.
- `phase7plus-roadmap.md`: proposed next phases beyond Phase 12.

## Cleanup note

Legacy Phase 0 audit/planning files were removed because they were stale and duplicated current implementation docs. Full history remains in Git.

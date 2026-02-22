# CodingAI Docs

Last verified: 2026-02-22 (UTC)  
Tracked branch: `localstate` (`/tmp/codingai-localstate`)

This folder documents the current implementation state and operating model of CodingAI.

## Start here

1. `current-state.md` for verified phase status and runtime findings.
2. `operations-runbook.md` for install/run/troubleshooting and revalidation checks.
3. `agent-architecture-current.md` for module and control-flow map.

## Validation snapshot

This docs refresh is based on live checks run against current code:

1. Import/compile sanity passed in venv (`main`, `service.api`, `compileall`).
2. API smoke checks passed (`/health`, `/repos`, `/policies`, `/repo/KRT-leadtool/summary`).
3. Policy loading resolves from `ai-agent/config/policies` with no parse errors.
4. Local LLM endpoint `127.0.0.1:11434` is currently not reachable; fallback path to OpenAI is functional.

## Index

- `current-state.md`: implementation status by phase (1-12), known gaps, and observed runtime status.
- `agent-architecture-current.md`: end-to-end control flow and module map.
- `strategy-mapping.md`: adaptive test strategy detection and selection behavior.
- `operations-runbook.md`: operational procedures and API/runtime checks.
- `local-llm-setup.md`: local model setup, fallback behavior, and telemetry controls.
- `phase8-api-control-plane.md`: FastAPI control plane endpoints and examples.
- `phase9-web-ui-mvp.md`: dashboard usage and controls.
- `phase10-governance-safety.md`: policy files, budgets, and dry-run behavior.
- `phase11-observability-ci.md`: structured events, metrics, and CI gate integration.
- `phase12-advanced-autonomy.md`: strategy-memory autonomy and staged test-patch fallback.
- `phase7plus-roadmap.md`: candidate phases beyond Phase 12.

# CodingAI Docs

Last verified: 2026-02-23 (UTC)  
Tracked branch: `localstate` (`/tmp/codingai-localstate`)

This folder documents the current implementation state and operating model of CodingAI.

## Start here

1. `current-state.md` for verified phase status and runtime findings.
2. `operations-runbook.md` for install/run/troubleshooting and revalidation checks.
3. `agent-architecture-current.md` for module and control-flow map.
4. `installation.md` for fresh Debian CLI install steps.
5. `roadmap.md` for phase progression (implemented + partial + pending phases).
6. `implementation-plan.md` for open roadmap gaps and concrete implementation steps.
7. `versioning-policy.md` for global docs versioning rules (optional for CodingAI, enforceable per workflow).

## Validation snapshot

This docs refresh is based on live checks run against current code:

1. Import/compile sanity passed (`python3 -m compileall -q ai-agent`).
2. API smoke: `/health`, `/repos`, `/policies`, `/repo/<repo>/summary`, `/research`.
3. Policy loading resolves from `ai-agent/config/policies` with no parse errors.
4. Local LLM endpoint status: not probed here; provider chain will fall back to OpenAI if configured.

## Index

- `current-state.md`: implementation status by phase (1-21), known gaps, and observed runtime status.
- `agent-architecture-current.md`: end-to-end control flow and module map.
- `strategy-mapping.md`: adaptive test strategy detection and selection behavior.
- `operations-runbook.md`: operational procedures and API/runtime checks.
- `installation.md`: clean install + first-run setup for Debian CLI hosts.
- `local-llm-setup.md`: local model setup, fallback behavior, and telemetry controls.
- `phase8-api-control-plane.md`: FastAPI control plane endpoints and examples.
- `phase9-web-ui-mvp.md`: dashboard usage and controls.
- `phase12-advanced-autonomy.md`: strategy-memory autonomy and staged test-patch fallback.
- `roadmap.md`: phases 1–21 (current and future, includes governance/observability).
- `implementation-plan.md`: actionable plan for features not fully implemented yet.
- `versioning-policy.md`: versioning scheme (`x1.x2.x3`) and bump criteria.

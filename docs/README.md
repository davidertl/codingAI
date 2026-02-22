# Phase 0 Runbook Docs

Audit date: 2026-02-22  
Scope root: `/home/codingai`

This folder contains the Phase 0 analysis deliverables for the local AI coding agent environment.

## How to use

1. Start with `phase0-summary.md` for executive status and priority queue.
2. Read `environment-map.md` and `full-inventory.md` for environment truth and scan coverage.
3. Use `agent-architecture-current.md`, `strategy-mapping.md`, and `observed-gaps-and-risks.md` for implementation planning.
4. Use `phase1-design-ready.md` as the implementation contract for Phase 1.

## Document index

- `environment-map.md`: host/runtime/toolchain map and topology.
- `full-inventory.md`: canonical inventory method, counts, and type breakdown.
- `agent-architecture-current.md`: actual code architecture and control/data flow.
- `observed-gaps-and-risks.md`: severity-ranked deltas and operational risks.
- `strategy-mapping.md`: strategy detection and behavior matrix.
- `phase1-design-ready.md`: decision-ready design for real patch generation + multi-file Git Data API commits.
- `phase0-summary.md`: concise summary and prioritized next actions.
- `inventory-canonical.tsv`: machine-readable file inventory (`path,size,ext,mime,class`).
- `local-llm-setup.md`: local LLM provider mode and Ollama bootstrap runbook.
- `phase7plus-roadmap.md`: phased implementation plan for remaining work (API, Web UI, governance, observability).
- `phase8-api-control-plane.md`: FastAPI control plane endpoints and worker model.
- `phase9-web-ui-mvp.md`: web dashboard usage and API backing endpoints.

## Notes

- Inventory snapshot includes files created during this Phase 0 run.
- Classification is MIME-based (`text` vs `binary`) and does not decode binary payloads.
- Sensitive values were observable in this dev environment and are documented at risk level only, not copied verbatim into these docs.

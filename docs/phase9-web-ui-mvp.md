# Phase 9 Web UI MVP

Source: `ai-agent/service/static/index.html`

## Purpose

Provide a lightweight operator dashboard on top of Phase 8 APIs.

## Access

1. Start API: `scripts/run_control_api.sh`
2. Open: `http://127.0.0.1:8000/` (or `/ui`)

## Available views and controls

1. Global status cards:
   - API health
   - LLM readiness/provider
   - active worker count
2. Repo controls:
   - select repo
   - start worker
   - stop worker
   - run once
   - manual refresh
3. Queue panel:
   - open `ai-fix` issues for selected repo
4. Tracked issue panel:
   - status from `state.json`
   - PR/check links
   - patch/test metadata
   - AI-stop/manual-approval indicators when present
5. Governance signals:
   - policy enabled/disabled
   - live vs dry-run mode
   - daily/weekly PR budget counters
6. CI observability signals:
   - per-issue CI gate status/reason in tracked issue cards
   - last processing duration per issue

## Refresh model

1. Polling-based updates (no websocket yet).
2. Backend responses come from:
   - `/health`
   - `/repos`
   - `/repo/{repo}/summary`
   - `/run/repo/{repo}`
   - `/stop/repo/{repo}`
   - `/run-once/repo/{repo}`

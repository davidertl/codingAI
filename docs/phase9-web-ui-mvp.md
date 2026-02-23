# Phase 9: Web UI MVP
Version: experimental-0.21.1

## Scope

Single-page dashboard served by the API process for operating and observing CodingAI.

## Main UI areas

1. Header/KPIs:
   - LLM readiness
   - Worker count
   - Active provider
   - Server time
2. Setup card:
   - GitHub owner/app/installation form
   - PEM upload form
   - Setup status indicators
3. Projects card:
   - Installation repo list
   - Enable/disable controls
4. Repo worker controls:
   - Select repo
   - Start/stop worker
   - Run once
   - Automation toggle
5. Summary panes:
   - Open AI-fix queue
   - Tracked issue pipeline/test state

## Current interaction model

1. Polling refresh loop (7s) for health, summaries, projects, setup.
2. Inline action toasts for success/failure feedback.
3. Tracked issue controls for diff/pause/resume/cancel when issue state exists.

## Verification

Dashboard controls are now exercised by the web strategy (`web_live_playwright`) in deterministic dashboard mode.

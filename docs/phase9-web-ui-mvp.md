# Phase 9 Web UI MVP

The control plane now exposes a browser UI for day-to-day operations and visibility.

## URL

- `GET /` (or `GET /ui`) serves the dashboard.

## UI capabilities

1. Dashboard status:
   - LLM readiness
   - active provider
   - worker count
2. Repo controls:
   - select repo
   - start worker
   - stop worker
   - run once
3. Queue visibility:
   - open `ai-fix` issues per repo
4. PR/test visibility:
   - tracked issue state from `state.json`
   - PR/check links
   - patch/test metadata and last status

## Backing endpoints used by UI

- `GET /health`
- `GET /repos`
- `GET /repo/{repo}/summary`
- `POST /run/repo/{repo}`
- `POST /stop/repo/{repo}`
- `POST /run-once/repo/{repo}`

## Run

```bash
scripts/run_control_api.sh
```

Then open:

```text
http://127.0.0.1:8000/
```

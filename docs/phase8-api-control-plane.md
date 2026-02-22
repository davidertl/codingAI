# Phase 8 API Control Plane

The agent now includes a FastAPI control plane that manages background workers per repository.

## Endpoints

- `GET /health`
- `GET /repos`
- `GET /state`
- `GET /workers`
- `POST /run/repo/{repo}`
- `POST /stop/repo/{repo}`

## Behavior

- Each started repo gets a dedicated background worker thread.
- Workers execute one `run_repo_cycle_once(repo)` pass, then sleep for `SERVICE_POLL_INTERVAL_SECONDS`.
- `state.json` remains the single runtime state source for agent decisions and history.
- `GET /state` exposes both persisted state and in-memory worker snapshots.

## Run

```bash
scripts/run_control_api.sh
```

Optional env:

- `HOST` (default `0.0.0.0`)
- `PORT` (default `8000`)
- `SERVICE_POLL_INTERVAL_SECONDS` (default inherits `POLL_INTERVAL`)

## Dependency note

Install dependencies in the agent venv:

```bash
/home/codingai/ai-agent/venv/bin/pip install -r /home/codingai/ai-agent/requirements.txt
```

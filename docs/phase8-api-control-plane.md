# Phase 8 API Control Plane

Source: `ai-agent/service/api.py`

## Purpose

Expose agent execution and state over HTTP with per-repo managed workers.

## Endpoints

1. `GET /health`
2. `GET /repos`
3. `GET /state`
4. `GET /workers`
5. `GET /queue/{repo}`
6. `GET /repo/{repo}/summary`
7. `GET /policies`
8. `GET /policy/{repo}`
9. `POST /run/repo/{repo}`
10. `POST /run-once/repo/{repo}`
11. `POST /stop/repo/{repo}`
12. `GET /` and `GET /ui` (serve dashboard HTML)
13. `GET /ui/static/*` (dashboard assets)

## Worker model

1. One background thread per started repo.
2. Each cycle runs `main.run_repo_cycle_once(repo)`.
3. Worker snapshots include cycle timing, error text, and cycle count.
4. `SERVICE_POLL_INTERVAL_SECONDS` controls sleep between cycles.

## Run

```bash
cd /home/codingai
scripts/run_control_api.sh
```

## Example calls

```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/run/repo/KRT-leadtool
curl -s http://127.0.0.1:8000/repo/KRT-leadtool/summary
curl -s -X POST http://127.0.0.1:8000/stop/repo/KRT-leadtool
```

## Notes

1. Control plane currently has no auth layer; keep deployment private.
2. `state.json` remains the runtime persistence store.

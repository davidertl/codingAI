# Phase 8: API Control Plane
Version: experimental-0.21.1

## Scope

FastAPI service exposing operational control, state visibility, and governance/observability endpoints for CodingAI workers.

## Core endpoints

1. `GET /health`
2. `GET /repos`
3. `POST /run/repo/{repo}`
4. `POST /stop/repo/{repo}`
5. `POST /run-once/repo/{repo}`
6. `GET /repo/{repo}/summary`
7. `GET /policies`
8. `GET /policy/{repo}`
9. `GET /metrics`
10. `GET /events`

## Setup/operations extensions

1. `GET /setup/status`
2. `GET /setup/values`
3. `POST /setup/github`
4. `POST /setup/pem`
5. `GET /projects`
6. `POST /projects/{repo}/enable`
7. `POST /projects/{repo}/disable`
8. `POST /self-checks`

## Runtime behavior

1. Worker lifecycle is managed by `WorkerManager`.
2. Automation scheduler starts repo workers based on persisted automation toggles.
3. State is persisted via `state.json` (planned migration to SQLite is tracked in `docs/implementation-plan.md`).

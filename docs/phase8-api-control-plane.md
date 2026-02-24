# Phase 8: API Control Plane
Version: experimental-0.23.0

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
11. `GET /state`
12. `GET /workers`

## Setup/operations extensions

1. `GET /setup/status`
2. `GET /setup/values`
3. `POST /setup/github`
4. `POST /setup/pem`
5. `GET /setup/audit`
6. `GET /projects`
7. `POST /projects/{repo}/enable`
8. `POST /projects/{repo}/disable`
9. `POST /projects/{repo}/push-gate`
10. `PUT /projects/{repo}/labels`

## Chat endpoints

1. `GET /chat/threads`
2. `POST /chat/threads`
3. `GET /chat/threads/{id}`
4. `DELETE /chat/threads/{id}`
5. `GET /chat/threads/{id}/messages`
6. `POST /chat/threads/{id}/messages`
7. `GET /chat/messages/{id}/stream` (SSE)
8. `POST /chat/threads/{id}/messages/{id}/attachments`
9. `GET /chat/files/{repo}/tree`
10. `GET /chat/files/{repo}/snippet`

## Orchestration endpoints

1. `GET /orchestration/runs`
2. `GET /orchestration/runs/{run_id}`
3. `GET /orchestration/runs/{run_id}/artifacts`

## Rules endpoints

1. `GET /rules/global`
2. `PUT /rules/global`
3. `DELETE /rules/global`
4. `GET /rules/projects/{repo}`
5. `PUT /rules/projects/{repo}`
6. `DELETE /rules/projects/{repo}`
7. `GET /rules/workers/{repo}`
8. `PUT /rules/workers/{repo}`
9. `DELETE /rules/workers/{repo}`

## Strategy Memory & Self-Tasks

1. `GET /strategy-memory`
2. `GET /strategy-memory/{repo}`
3. `GET /self-tasks`
4. `GET /self-tasks/{id}`
5. `POST /self-tasks/{id}/snooze`
6. `POST /self-tasks/{id}/dismiss`
7. `POST /self-tasks/scan`

## WebSocket

1. `WS /ws/live` — bidirectional live dashboard connection; supports `set_repo`, `ping`, `chat_send` messages; pushes periodic snapshots including orchestration runs.

## Research

1. `GET /research?q=...` — web search via SearxNG

## Runtime behavior

1. Worker lifecycle is managed by `WorkerManager`.
2. Automation scheduler starts repo workers based on persisted automation toggles.
3. State is persisted via SQLite (`state.db`) in a Docker named volume (`codingai-db`).
4. Strategy memory and self-tasks are stored in `state.db`.

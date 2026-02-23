# Operations Runbook
Version: experimental-0.21.1

## Prerequisites

1. Python 3.13 with virtualenv at `ai-agent/venv`
2. Docker + Docker Compose
3. Valid GitHub App credentials in `ai-agent/.env` and `ai-agent/github_app/*.pem`
4. Network access to GitHub API (and OpenAI/local LLM as configured)
5. Secrets readable by container user (see Secrets section)

## Install dependencies

```bash
cd ai-agent
source venv/bin/activate
pip install -r requirements.txt
```

## Fresh install (Debian CLI)

Use the interactive installer:

```bash
bash scripts/install_codingai.sh
```

It will:

1. install OS packages (Python, Node 20, build tools, Playwright deps, Docker, Compose plugin)
2. create `ai-agent/venv` and install Python deps
3. write `ai-agent/.env` interactively
4. remind you to copy the GitHub App `.pem` into `ai-agent/github_app/`

## Secrets & volumes

- Ensure `ai-agent/.env` exists with `chmod 600`; it is bind-mounted into the container.
- Ensure `ai-agent/github_app/` exists and the private key inside has `chmod 600`; directory can be `chmod 700`.
- Compose already mounts both locations read-write so the UI `/setup/*` endpoints can write them; if you override paths with env vars, update `docker-compose.yaml` accordingly.
- Never paste the PEM contents into logs; use the UI upload or copy the file onto disk with correct permissions.

## Pipeline pause/diff controls (Phase 16)

- API endpoints:
  - `GET /pipeline/{repo}/{issue}` → current pipeline state
  - `GET /pipeline/{repo}/{issue}/diff` → last captured diff (truncated)
  - `POST /pipeline/{repo}/{issue}/pause|resume|cancel` → control pause window
- Default pause window before tests: 10 seconds (`CODINGAI_PAUSE_WINDOW_SECONDS`), max pause cap 300 seconds (`CODINGAI_MAX_PAUSE_SECONDS`).
- Dashboard tracked-issues cards expose buttons for diff/pause/resume/cancel.

## Job worktrees & cleanup (Phase 17)

- Mirrors live under `workspaces/repos/{repo}`; job-specific worktrees under `workspaces/jobs/{repo}/{job_id}`.
- TTL cleanup runs after each repo cycle: default 24h (`CODINGAI_JOB_TTL_SECONDS`), keeps at most 12 jobs per repo (`CODINGAI_JOB_MAX_PER_REPO`).
- Job worktrees are created at the exact base SHA from GitHub before patch/test; git worktree add is used instead of in-place cloning.

## Revalidation checklist

Run this after pulling updates or before enabling workers:

```bash
cd ai-agent
PYTHONPATH=. ./venv/bin/python -m compileall -q .
PYTHONPATH=. ./venv/bin/python -c "import main; import service.api; print('import_ok')"
```

API smoke check:

```bash
cd ai-agent
PYTHONPATH=. ./venv/bin/python -m uvicorn service.api:app --host 127.0.0.1 --port 8010
```

In a second shell:

```bash
curl -s http://127.0.0.1:8010/health
curl -s http://127.0.0.1:8010/repos
curl -s http://127.0.0.1:8010/policies
curl -s http://127.0.0.1:8010/repo/<repo-name>/summary
```

## Run modes

### 1. CLI loop mode

```bash
cd ai-agent
source venv/bin/activate
python main.py
```

### 2. API/control-plane mode

```bash
cd <repo-root>
scripts/run_control_api.sh
```

### 3. Web UI mode

Start API mode, then open:

`http://<host>:8000/` (or `/ui`)

### 4. Docker Compose mode

```bash
CODINGAI_HTTP_PORT=8000 docker compose up --build
```

Volumes (host): `.env`, `github_app/`, `logs/`, `state.json`, `workspaces/`.

## Common env toggles

1. `TARGET_REPOS=<repo-a>,<repo-b>`
2. `MANUAL_APPROVAL_REQUIRED=true`
3. `PR_AUTO_UPDATE_ENABLED=true`
4. `ENABLE_GITHUB_CHECKS=true`
5. `AUTO_GENERATE_TEST_PATCHES=false`
6. `STRATEGY_SWITCH_CONFIDENCE_THRESHOLD=0.65`

For local-model operation, see `local-llm-setup.md`.  
Note: current VM state has no local model server listening on `127.0.0.1:11434`; OpenAI is the active provider via fallback.

## Policy-driven governance

Policy files are loaded from `ai-agent/config/policies/`:

1. `default.json`
2. `<repo>.json` overrides

Use policy files for:

1. manual approval rules
2. daily/weekly PR budgets
3. branch naming template
4. dry-run mode (`"dry_run": true`)
5. autonomy tuning (`strategy.*` and `patch.test_patch_*`)

## Health and quick checks

### API health

```bash
curl -s http://127.0.0.1:8000/health
```

### Repo summary

```bash
curl -s http://127.0.0.1:8000/repo/<repo-name>/summary
```

### Policy inspection

```bash
curl -s http://127.0.0.1:8000/policy/<repo-name>
curl -s http://127.0.0.1:8000/policies
```

### Observability inspection

```bash
curl -s http://127.0.0.1:8000/metrics
curl -s http://127.0.0.1:8000/metrics/json
curl -s "http://127.0.0.1:8000/events?limit=50&repo=<repo-name>"
```

### CI status inspection

```bash
curl -s http://127.0.0.1:8000/ci/<repo-name>/1
```

### Autonomy policy quick example

```json
{
  "patch": {
    "auto_generate_test_patches": true,
    "test_patch_on_failure_only": true,
    "test_patch_min_confidence": 0.6
  },
  "strategy": {
    "max_attempts": 4,
    "quarantine_threshold": 3,
    "quarantine_seconds": 43200,
    "memory_half_life_seconds": 604800
  }
}
```

### Run one cycle

```bash
curl -s -X POST http://127.0.0.1:8000/run-once/repo/<repo-name>
```

## Troubleshooting

1. `LLM not ready`:
   - Verify provider env variables.
   - Check endpoint reachability.
   - Inspect `ai-agent/logs/llm_telemetry.jsonl`.
2. `No patch ops generated`:
   - Check issue quality/body detail.
   - Check model response quality and provider errors.
3. Branch/PR drift:
   - Review `state.json` issue entry (`active_branch`, `source_issue_updated_at`).
4. API worker stuck:
   - `GET /workers`, then `POST /stop/repo/{repo}` and restart.
5. Local LLM expected but not used:
   - Check `curl -s http://127.0.0.1:11434/v1/models`.
   - If unavailable, start Ollama via `scripts/setup_local_llm_ollama.sh`.
   - Confirm provider behavior from `GET /health` and `state.json -> llm_runtime`.

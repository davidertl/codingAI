# Operations Runbook

## Prerequisites

1. Python 3.13 with virtualenv at `ai-agent/venv`
2. Docker + Docker Compose
3. Valid GitHub App credentials in `ai-agent/.env` and `ai-agent/github_app/*.pem`
4. Network access to GitHub API (and OpenAI/local LLM as configured)

## Install dependencies

```bash
cd /home/codingai/ai-agent
source venv/bin/activate
pip install -r requirements.txt
```

## Run modes

### 1. CLI loop mode

```bash
cd /home/codingai/ai-agent
source venv/bin/activate
python main.py
```

### 2. API/control-plane mode

```bash
cd /home/codingai
scripts/run_control_api.sh
```

### 3. Web UI mode

Start API mode, then open:

`http://<host>:8000/` (or `/ui`)

## Common env toggles

1. `TARGET_REPOS=KRT-leadtool,KRT-Com_Discord`
2. `MAX_PRS_PER_REPO_PER_DAY=3`
3. `MANUAL_APPROVAL_REQUIRED=true`
4. `PR_AUTO_UPDATE_ENABLED=true`
5. `ENABLE_GITHUB_CHECKS=true`
6. `AUTO_GENERATE_TEST_PATCHES=false`
7. `STRATEGY_SWITCH_CONFIDENCE_THRESHOLD=0.65`

For local-model operation, see `local-llm-setup.md`.

## Policy-driven governance

Policy files are loaded from `ai-agent/config/policies/`:

1. `default.json`
2. `<repo>.json` overrides

Use policy files for:

1. manual approval rules
2. daily/weekly PR budgets
3. branch naming template
4. dry-run mode (`"dry_run": true`)

## Health and quick checks

### API health

```bash
curl -s http://127.0.0.1:8000/health
```

### Repo summary

```bash
curl -s http://127.0.0.1:8000/repo/KRT-leadtool/summary
```

### Policy inspection

```bash
curl -s http://127.0.0.1:8000/policy/KRT-leadtool
curl -s http://127.0.0.1:8000/policies
```

### Run one cycle

```bash
curl -s -X POST http://127.0.0.1:8000/run-once/repo/KRT-leadtool
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

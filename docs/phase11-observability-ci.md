# Phase 11 Observability and CI Integration

Phase 11 adds structured pipeline telemetry, Prometheus-style metrics, and CI-aware gating for iterative PR updates.

## Observability implementation

Core module: `ai-agent/core/observability.py`

1. Structured event log (JSONL):
   - default file: `/home/codingai/ai-agent/logs/events.jsonl`
   - env controls:
     - `OBS_EVENT_LOG_ENABLED=true|false`
     - `OBS_EVENT_LOG_FILE=/path/to/events.jsonl`
2. In-process metric registry:
   - counters
   - gauges
   - duration summaries (`*_sum`, `*_count`)
3. Prometheus text rendering:
   - exported via API `GET /metrics`

## Event schema

Each event entry contains:

1. `ts` (unix seconds)
2. `time_utc` (ISO UTC)
3. `event` (event name)
4. Optional: `repo`, `issue_number`, `status`, `duration_ms`, `data`

## Main pipeline instrumentation

In `ai-agent/main.py`, events/metrics are emitted for:

1. repo cycle start/end
2. issue start and terminal outcome
3. patch generation timing
4. test execution timing and attempts
5. CI gate checks (pass/block/error)
6. GitHub write completion

## CI-aware update gating

CI data source: `ai-agent/github/ci_status.py`

1. PR head SHA lookup
2. combined commit status lookup
3. check-runs lookup
4. normalized CI summary used for gating decisions

Policy controls (`policy.ci`):

1. `require_green_before_update`
2. `block_on_pending`
3. `block_on_failed`
4. `require_checks_present`
5. `allowed_check_conclusions`
6. `on_error` (`allow` or `block`)
7. `retry_after_seconds`

Gate is evaluated only for iterative updates (existing PR path).

## API additions

From `ai-agent/service/api.py`:

1. `GET /metrics` (Prometheus text)
2. `GET /metrics/json`
3. `GET /events?limit=100&repo=<repo>&event=<event>`
4. `GET /ci/{repo}/{pr_number}`

`GET /repo/{repo}/summary` now includes CI gate state in tracked issue data.

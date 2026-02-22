# Phase 10 Governance and Safety Expansion

Phase 10 is implemented with policy-driven controls, explicit budgets, and dry-run mode.

## Policy files

Location: `ai-agent/config/policies/`

1. `default.json`: baseline policy applied to all repos.
2. `<repo>.json`: per-repo override by repository name.

Current repo files:

1. `KRT-leadtool.json`
2. `KRT-Com_Discord.json`

## Effective policy model

Resolved by `ai-agent/core/policy.py`:

1. env defaults
2. `default.json` overlay
3. per-repo overlay
4. normalization/clamping

Main policy sections:

1. `enabled`
2. `dry_run`
3. `safety` (`ai_stop_phrase`, failure cooldown, failure issue publish toggle)
4. `approval` (required + labels + token)
5. `pr` (auto-update, daily/weekly caps, checks config)
6. `patch` (patch/test patch operation limits)
7. `strategy` (confidence threshold)
8. `branch` (branch naming template)

## Budget enforcement

Implemented in `ai-agent/main.py`:

1. Daily PR cap: `pr.max_per_day`
2. Weekly PR cap: `pr.max_per_week`
3. Counters persisted in `state.json`:
   - `daily_pr_counts`
   - `weekly_pr_counts`

Limits apply to new PR creation only and are skipped in dry-run mode.

## Dry-run mode

Enable per repo via policy:

```json
{
  "dry_run": true
}
```

Dry-run behavior:

1. Executes clone/update, patch generation, local patch apply, and tests.
2. Skips all GitHub write operations:
   - failure issue creation
   - branch create/update
   - commit creation
   - PR create/update comments
   - check-run publication
3. Writes dry-run outcomes to `state.json` (`last_status=dry_run_passed|dry_run_failed`, `dry_run_actions`).

## API visibility additions

From `ai-agent/service/api.py`:

1. `GET /policies`
2. `GET /policy/{repo}`
3. `GET /repo/{repo}/summary` now includes `policy` and `budget` sections.

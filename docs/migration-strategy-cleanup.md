# Strict Cleanup Migration Strategy
Version: experimental-0.23.0

This cleanup removes old naming patterns while keeping runtime behavior compatible for existing installs.

## 1) GitHub App PEM filename

- Canonical filename is now `ai-agent/github_app/github-app.pem`.
- Installer behavior:
  - If canonical file exists, it is used.
  - If canonical file is missing and any `.pem` exists in `ai-agent/github_app/`, the first found file is copied to `github-app.pem`.
- Runtime behavior (`ai-agent/paths.py`):
  - Uses `GITHUB_APP_PEM` when explicitly set.
  - Otherwise prefers `github-app.pem`.
  - If missing, falls back to the first `.pem` found in the directory.

## 2) Runner mode naming

- Non-sandbox local execution mode is named `local_runner`.
- `sandbox_api` mode is unchanged.
- Compatibility behavior:
  - Any `RUNNER_MODE` value other than `sandbox_api` continues to run in local mode.
  - Existing deployments do not need an immediate `.env` change.

## 3) Projects state migration

- Source of truth remains SQLite (`ai-agent/state.db` `projects` table).
- During migration from older state snapshots, `state.json` key `projects_enabled` is still consumed to seed initial rows.
- After seeding, ongoing project toggles are persisted in SQLite as before.

## 4) Failure reporting policy keys

- Preferred keys:
  - `safety.publish_failure_comment`
  - `safety.publish_failure_subissue`
  - `safety.failure_subissue_threshold`
- Compatibility behavior:
  - `safety.publish_failure_issue` is still accepted as a fallback input signal for subissue behavior.

## Rollout checklist

1. Pull latest branch.
2. Run `bash -n scripts/install_codingai.sh`.
3. Verify health: `curl -s http://127.0.0.1:8000/health`.
4. Confirm setup: `curl -s http://127.0.0.1:8000/setup/status`.
5. If needed, normalize PEM filename manually:
   - `cp ai-agent/github_app/<existing>.pem ai-agent/github_app/github-app.pem`
   - `chmod 600 ai-agent/github_app/github-app.pem`

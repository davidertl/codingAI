# Strict Cleanup Notes
Version: experimental-0.23.0

This cleanup removes transitional compatibility paths because there are no historical states to preserve.

## 1) GitHub App PEM filename

- Canonical filename is now `ai-agent/github_app/github-app.pem`.
- Installer and runtime now use this path directly unless `GITHUB_APP_PEM` is explicitly set.

## 2) Runner mode naming

- Non-sandbox local execution mode is named `local_runner`.
- `sandbox_api` mode is unchanged.

## 3) Projects seeding

- Source of truth remains SQLite (`ai-agent/state.db` `projects` table).
- New projects are seeded from current repository discovery defaults only.

## 4) Failure reporting policy keys

- Preferred keys:
  - `safety.publish_failure_comment`
  - `safety.publish_failure_subissue`
  - `safety.failure_subissue_threshold`
- Deprecated compatibility handling for old failure-report keys has been removed.

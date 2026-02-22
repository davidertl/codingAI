# Agent Architecture (Current)

## High-level flow

Primary loop (`ai-agent/main.py`):

1. Select repository from static list.
2. Poll issues labeled `ai-fix`.
3. For each issue:
   - clone/update repo workspace
   - create/reset `ai/issue-{n}` branch locally
   - append placeholder `AI_CHANGE.txt`
   - run adaptive test runner
4. On success:
   - create blob/tree/commit via GitHub Git Data API
   - update branch ref (no force in API path)
   - create or reuse PR
   - persist state as passed/pr_created
5. On failure:
   - create failure issue
   - persist cooldown/retry metadata in `state.json`

## Component map

- Orchestration: `ai-agent/main.py`
- Repo operations: `ai-agent/github/repo_manager.py`
- GitHub App auth token: `ai-agent/github/app_auth.py`
- Git Data API commit operations: `ai-agent/github/git_api_commit.py`
- Issue and PR API calls: `ai-agent/github/issue_manager.py`, `ai-agent/github/pr_manager.py`
- Adaptive strategy engine: `ai-agent/core/test_runner.py`
- LLM strategy selector: `ai-agent/llm/strategy_llm.py`
- Local config/state: `ai-agent/config/repos.yaml`, `ai-agent/state.json`, `ai-agent/.env`

## Observed implementation details

### GitHub App and commit identity path

- Installation token is generated from app JWT and cached in process memory.
- Commit path uses GitHub Git Data API (`blobs`, `trees`, `commits`, `refs`).
- Branch update in API path does not pass force flag.

### Local workspace behavior

- Existing repo update uses `git fetch` + `git reset --hard origin/main`.
- AI branch creation uses `git checkout -B ai/issue-{n}`.

### Adaptive test behavior

- Repo analysis checks markers:
  - `docker-compose.yml`
  - root `Dockerfile`
  - `.csproj` / `.sln`
  - `package.json`
- Strategy order (deterministic first):
  1. docker compose build
  2. docker build
  3. dotnet build in SDK container
  4. dotnet build with `EnableWindowsTargeting=true`
  5. node build in `node:20` container
- On failure, calls OpenAI `/v1/responses` to select next strategy from remaining only.
- Fallback behavior exists for missing API key, API errors, malformed JSON, and invalid strategy IDs.

## Confirmed architecture blockers

1. Import mismatch in orchestration:
   - `main.py` imports `create_or_update_branch` from `git_api_commit.py`, but symbol is absent.
2. Undefined function call in success path:
   - `main.py` calls `create_branch(...)` but does not import `create_branch`.

## Data/state shape currently present

`state.json` contains per-repo per-issue entries such as:

- `pr_created: bool`
- `last_status: "passed" | "failed"`
- `retry_after: unix_timestamp`
- `last_error` (on failures)

No PR comment tracking, AI stop flags, or strategy memory objects are currently present.

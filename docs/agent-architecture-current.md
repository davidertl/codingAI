# Agent Architecture (Current)

## Core pipeline

`ai-agent/main.py` drives a per-repo cycle:

1. Validate LLM readiness (`llm/provider.py`).
2. Fetch open GitHub issues labeled `ai-fix`.
3. For each issue:
   - enforce safety gates (AI Stop, manual approval, cooldown, daily PR cap),
   - load repo policy (budgets, branch template, dry-run mode),
   - resolve/create iteration branch naming (`ai/issue-<n>-iter-<k>`),
   - generate structured patch ops with LLM (`llm/patch_llm.py`),
   - apply patch locally and run adaptive tests (`core/test_runner.py`),
   - commit via Git Data API only on pass (`github/git_api_commit.py`) unless dry-run,
   - create/reuse PR, upsert PR report comment, optionally publish check-run.

## Module map

1. `main.py`
   - orchestration, safety policy, state writes, patch/test/PR workflow.
2. `core/test_runner.py`
   - repo analysis, strategy execution, LLM-guided fallback, memory scoring.
3. `core/policy.py`
   - policy-file loading, repo override merge, normalization.
4. `llm/provider.py`
   - provider selection (`openai/local/auto`), health checks, failover, telemetry.
5. `llm/strategy_llm.py`
   - next-strategy selector with retry/backoff behavior.
6. `llm/patch_llm.py`
   - patch and optional test-patch generation with strict schema validation.
7. `github/app_auth.py`
   - GitHub App JWT + installation token lifecycle.
8. `github/git_api_commit.py`
   - blob/tree/commit/ref APIs, multi-file tree assembly.
9. `github/issue_manager.py`
   - issue polling and failure issue creation.
10. `github/pr_manager.py`
   - PR create/reuse, comment upsert, AI Stop detection.
11. `github/checks_manager.py`
    - completed check-run publication.
12. `service/api.py`
    - FastAPI control plane and worker manager.
13. `service/static/index.html`
    - web dashboard for operations and visibility.

## State model (runtime)

`state.json` is runtime-generated (ignored in Git). Common keys:

1. Per issue (`state[repo][issue_number]`):
   - `active_branch`, `pr_created`, `pr_number`, `pr_url`
   - `last_status`, `last_error`, `retry_after`
   - `patch_ops_count`, `patch_confidence`
   - `report_comment_id`, `check_run_url`
   - `pending_manual_approval`
   - `ai_stopped`, `ai_stopped_at`, `ai_stop_reason`
2. Global/runtime:
   - `daily_pr_counts`
   - `strategy_memory`
   - `pr_controls`
   - `llm_runtime`

## Safety properties

1. Commit path does not use git push/force push; uses GitHub Git Data API ref updates.
2. Remote branch is created only after local patched tests pass.
3. AI Stop comment disables further processing for the associated issue/PR.
4. Optional manual approval can gate all issue execution.
5. Daily PR cap prevents unbounded PR creation.
6. Weekly PR cap and dry-run mode are policy-controlled per repository.

# Observed Gaps And Risks

## Severity model

- `Critical`: blocks runtime or can cause destructive/unsafe operation.
- `High`: major behavior gap vs roadmap and safety goals.
- `Medium`: functional limitation with controlled impact.
- `Low`: quality/operability improvement area.

## Findings (ordered by severity)

1. `Critical` Runtime blocker: unresolved import in main loop
   - Evidence: `ai-agent/main.py` imports `create_or_update_branch`; symbol not defined in `ai-agent/github/git_api_commit.py`.
   - Impact: agent cannot start successfully in venv.

2. `High` Potential runtime error path in success flow
   - Evidence: `ai-agent/main.py` calls `create_branch(...)` but imported names do not include `create_branch`.
   - Impact: branch creation path can fail when issue branch does not yet exist remotely.

3. `High` Destructive local workspace reset
   - Evidence: `ai-agent/github/repo_manager.py` uses `git reset --hard origin/main` during update.
   - Impact: local uncommitted repo changes in workspace are discarded.

4. `High` Force push still exists in legacy/helper path
   - Evidence: `ai-agent/github/repo_manager.py` `push_branch()` and `ai-agent/test_git_push.py` use `git push --force`.
   - Impact: contradicts stated no-force policy if those paths are used.

5. `High` Phase 1 not implemented (placeholder commit content)
   - Evidence: `AI_CHANGE.txt` is appended and a single blob/tree entry is created.
   - Impact: no real patch generation or multi-file commit tree.

6. `Medium` No PR comment automation
   - Evidence: no GitHub issue/PR comment upsert function or comment ID state in current modules.

7. `Medium` No AI stop mechanism
   - Evidence: no polling/parsing of PR comments for stop keyword, no state fields for stop control.

8. `Medium` Limited API resilience for rate limits
   - Evidence: OpenAI call handles non-200 fallback but no exponential backoff/jitter for repeated 429.

9. `Medium` Error payload trimming is coarse
   - Evidence: full strategy output passed to issue body, partial clipping only by substring in state.

10. `Low` Config duplication / drift risk
   - Evidence: `config/repos.yaml` exists but main loop repository list is hardcoded in `main.py`.

## Roadmap delta mapping (from your phase list)

- Phase 1 (real patch generation): `Not implemented`
- Phase 2 (PR comment update): `Not implemented`
- Phase 3 (AI Stop from PR comment): `Not implemented`
- Phase 4 (429 backoff, log extraction, confidence threshold, memory): `Partially implemented` (basic fallbacks only)
- Phase 5 (safe autonomous mode guards): `Not implemented`
- Phase 6 (advanced optional features): `Not implemented`

## Security exposure observations (dev environment)

- Credentials and private key materials are present in local files (`.env`, app pem path).
- This is acceptable for this dev audit context per instruction, but production hardening requires secret handling controls and redaction discipline in logs/docs.

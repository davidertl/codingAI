# Environment Map

## Host baseline

- OS: Debian GNU/Linux 13 (trixie)
- Kernel: `6.12.73+deb13-amd64`
- User home: `/home/codingai`
- Date of capture: 2026-02-22

## Toolchain baseline

- `git 2.47.3`
- `docker 26.1.5`
- `docker compose 2.26.1`
- `node v20.19.2`
- `npm 9.2.0`
- `python3 3.13.5`
- `ai-agent` venv python: `3.13.5`
- `gh 2.46.0`

## Top-level layout (`/home/codingai`)

- `.vscode-server`: editor server/extensions/cache (largest file count)
- `workspaces`: repo worktrees for test subjects
- `ai-agent`: autonomous agent code + venv + app credentials/state
- `.npm`, `.cache`: package/download caches
- `.codex`: Codex runtime data
- `docs`: Phase 0 output

## Repository/worktree state (captured)

- `workspaces/KRT-leadtool`: branch `ai/issue-1`
- `workspaces/KRT-leadtool-test`: branch `ai/app-test`
- `workspaces/KRT-Com_Discord`: branch `ai/issue-6`, behind remote by 1, untracked `AI_CHANGE.txt`

## Runtime import sanity

From `/home/codingai/ai-agent`:

- Host python import: `python3 -c "import main"`
  - Fails with `ModuleNotFoundError: No module named 'jwt'`
- Venv import: `./venv/bin/python -c "import main"`
  - Fails with `ImportError: cannot import name 'create_or_update_branch' from github.git_api_commit`

Interpretation:

- Host runtime missing dependency is expected if venv is required.
- Venv failure is a real code integration blocker (symbol imported but not implemented/exported).

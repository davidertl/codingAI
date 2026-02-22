import os
import subprocess

GITHUB_OWNER = "davidertl"
WORKSPACE_ROOT = "/home/codingai/workspaces"


def _run(cmd, *, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _remote_default_branch(repo_path):
    # Example output: refs/remotes/origin/main
    r = _run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_path, check=False)
    if r.returncode == 0:
        ref = (r.stdout or "").strip()
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix):
            branch = ref[len(prefix):]
            if branch:
                return branch
    return "main"


def clone_or_update(repo_name):
    repo_path = f"{WORKSPACE_ROOT}/{repo_name}"

    if os.path.exists(repo_path):
        _run(["git", "fetch", "--prune"], cwd=repo_path)
        default_branch = _remote_default_branch(repo_path)
        _run(["git", "checkout", "-B", default_branch, f"origin/{default_branch}"], cwd=repo_path)
        _run(["git", "reset", "--hard", f"origin/{default_branch}"], cwd=repo_path)
    else:
        _run(
            ["git", "clone", f"https://github.com/{GITHUB_OWNER}/{repo_name}.git"],
            cwd=WORKSPACE_ROOT,
        )

    return repo_path


def create_ai_branch(repo_path, issue_number):
    branch_name = f"ai/issue-{issue_number}"
    _run(["git", "checkout", "-B", branch_name], cwd=repo_path)
    return branch_name


def commit_all(repo_path, message):
    _run(["git", "add", "."], cwd=repo_path)
    _run(["git", "commit", "-m", message], cwd=repo_path, check=False)


def push_branch(repo_path, branch):
    raise RuntimeError(
        "Direct git push is disabled for safety. "
        "Use GitHub Git Data API commit flow instead."
    )

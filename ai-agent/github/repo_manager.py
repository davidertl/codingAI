import os
import subprocess
import time
import shutil

import requests

from github.config import get_github_owner
from paths import WORKSPACES_DIR

WORKSPACE_ROOT = str(WORKSPACES_DIR)
REPOS_ROOT = os.path.join(WORKSPACE_ROOT, "repos")
JOBS_ROOT = os.path.join(WORKSPACE_ROOT, "jobs")

JOB_TTL_SECONDS = int(os.getenv("CODINGAI_JOB_TTL_SECONDS", "86400") or "86400")
JOB_MAX_PER_REPO = int(os.getenv("CODINGAI_JOB_MAX_PER_REPO", "12") or "12")
DISK_WARN_THRESHOLD_BYTES = int(os.getenv("CODINGAI_DISK_WARN_THRESHOLD_BYTES", str(5 * 1024 * 1024 * 1024)) or str(5 * 1024 * 1024 * 1024))


def list_installation_repos(token: str) -> list[str]:
    """
    List repositories accessible to the GitHub App installation.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    repos = []
    page = 1
    while True:
        r = requests.get(
            f"https://api.github.com/installation/repositories",
            headers=headers,
            params={"per_page": 100, "page": page},
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("repositories", []) if isinstance(data, dict) else []
        repos.extend([item.get("name") for item in items if item.get("name")])
        if len(items) < 100:
            break
        page += 1
    return repos


def _run(cmd, *, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def ensure_repo_mirror(repo_name: str) -> str:
    owner = get_github_owner()
    if not owner:
        raise RuntimeError("GITHUB_OWNER is not configured")
    os.makedirs(REPOS_ROOT, exist_ok=True)
    repo_path = os.path.join(REPOS_ROOT, repo_name)

    if os.path.exists(repo_path):
        _run(["git", "fetch", "--prune"], cwd=repo_path)
    else:
        _run(
            ["git", "clone", f"https://github.com/{owner}/{repo_name}.git", repo_path],
            cwd=REPOS_ROOT,
        )
    return repo_path


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
    """
    Legacy path used by earlier phases. Kept for compatibility but prefer prepare_job_worktree.
    """
    mirror = ensure_repo_mirror(repo_name)
    default_branch = _remote_default_branch(mirror)
    _run(["git", "checkout", "-B", default_branch, f"origin/{default_branch}"], cwd=mirror)
    _run(["git", "reset", "--hard", f"origin/{default_branch}"], cwd=mirror)
    return mirror


def prepare_job_worktree(repo_name: str, job_id: str, base_sha: str) -> str:
    """
    Create/update a per-job worktree rooted under workspaces/jobs/{repo}/{job_id}
    using the shared mirror clone under workspaces/repos/{repo}.
    """
    mirror = ensure_repo_mirror(repo_name)
    os.makedirs(os.path.join(JOBS_ROOT, repo_name), exist_ok=True)
    job_path = os.path.join(JOBS_ROOT, repo_name, job_id)

    if os.path.exists(job_path):
        _run(["git", "worktree", "remove", "--force", job_path], cwd=mirror, check=False)
        shutil.rmtree(job_path, ignore_errors=True)

    _run(["git", "worktree", "add", "--detach", job_path, base_sha], cwd=mirror)
    return job_path


def cleanup_jobs(now_ts: int | None = None):
    """
    Best-effort cleanup of old job worktrees/directories to keep disk small.
    Removes directories older than JOB_TTL_SECONDS and trims count per repo.
    """
    now_ts = now_ts or int(time.time())
    if not os.path.exists(JOBS_ROOT):
        return

    for repo_name in os.listdir(JOBS_ROOT):
        repo_dir = os.path.join(JOBS_ROOT, repo_name)
        if not os.path.isdir(repo_dir):
            continue
        entries = []
        for job_id in os.listdir(repo_dir):
            job_path = os.path.join(repo_dir, job_id)
            try:
                st = os.stat(job_path)
                mtime = int(st.st_mtime)
            except FileNotFoundError:
                continue
            entries.append((mtime, job_path))

        entries.sort()  # oldest first
        # remove by age
        for mtime, path in entries:
            if now_ts - mtime > JOB_TTL_SECONDS:
                shutil.rmtree(path, ignore_errors=True)
        # enforce max keep
        entries = [(m, p) for m, p in entries if os.path.exists(p)]
        if len(entries) > JOB_MAX_PER_REPO:
            extra = entries[:-JOB_MAX_PER_REPO]
            for _mtime, path in extra:
                shutil.rmtree(path, ignore_errors=True)


def disk_usage_report() -> dict:
    total_bytes = 0
    for root, dirs, files in os.walk(WORKSPACES_DIR):
        for f in files:
            try:
                total_bytes += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    return {
        "workspaces_bytes": total_bytes,
        "warn": total_bytes >= DISK_WARN_THRESHOLD_BYTES,
        "threshold_bytes": DISK_WARN_THRESHOLD_BYTES,
    }


def push_branch(repo_path, branch):
    raise RuntimeError(
        "Direct git push is disabled for safety. "
        "Use GitHub Git Data API commit flow instead."
    )

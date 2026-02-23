import os
import subprocess
import time
import shutil
import base64
from datetime import datetime, timezone

import requests

from github.app_auth import get_installation_token
from github.config import get_github_owner
from paths import WORKSPACES_DIR

WORKSPACE_ROOT = str(WORKSPACES_DIR)
REPOS_ROOT = os.path.join(WORKSPACE_ROOT, "repos")
JOBS_ROOT = os.path.join(WORKSPACE_ROOT, "jobs")

JOB_TTL_SECONDS = int(os.getenv("CODINGAI_JOB_TTL_SECONDS", "86400") or "86400")
JOB_MAX_PER_REPO = int(os.getenv("CODINGAI_JOB_MAX_PER_REPO", "12") or "12")
DISK_WARN_THRESHOLD_BYTES = int(os.getenv("CODINGAI_DISK_WARN_THRESHOLD_BYTES", str(5 * 1024 * 1024 * 1024)) or str(5 * 1024 * 1024 * 1024))
INSTALL_REPOS_CACHE_SECONDS = int(os.getenv("CODINGAI_INSTALLATION_REPOS_CACHE_SECONDS", "60") or "60")
INSTALL_REPOS_MIN_RETRY_SECONDS = int(os.getenv("CODINGAI_INSTALLATION_REPOS_MIN_RETRY_SECONDS", "15") or "15")

_installation_repos_cache: list[str] = []
_installation_repos_cached_at = 0.0
_installation_repos_cooldown_until = 0.0
_installation_repos_last_error = ""


def list_installation_repos(token: str) -> list[str]:
    """
    List repositories accessible to the GitHub App installation.
    Uses short-lived cache and cooldown/backoff to avoid exhausting
    GitHub App installation rate limits when UI polling is frequent.
    """
    global _installation_repos_cache
    global _installation_repos_cached_at
    global _installation_repos_cooldown_until
    global _installation_repos_last_error

    now = time.time()
    if _installation_repos_cache and INSTALL_REPOS_CACHE_SECONDS > 0:
        if (now - _installation_repos_cached_at) <= INSTALL_REPOS_CACHE_SECONDS:
            return list(_installation_repos_cache)

    if _installation_repos_cooldown_until > now:
        if _installation_repos_cache:
            return list(_installation_repos_cache)
        retry_at = datetime.fromtimestamp(_installation_repos_cooldown_until, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        raise RuntimeError(
            f"Installation repositories temporarily unavailable until {retry_at} ({_installation_repos_last_error or 'cooldown active'})"
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    repos = []
    page = 1
    last_response = None
    try:
        while True:
            r = requests.get(
                "https://api.github.com/installation/repositories",
                headers=headers,
                params={"per_page": 100, "page": page},
                timeout=15,
            )
            last_response = r
            r.raise_for_status()
            data = r.json()
            items = data.get("repositories", []) if isinstance(data, dict) else []
            repos.extend([item.get("name") for item in items if item.get("name")])
            if len(items) < 100:
                break
            page += 1
    except requests.RequestException as e:
        cooldown_until = time.time() + max(1, INSTALL_REPOS_MIN_RETRY_SECONDS)
        if last_response is not None:
            retry_after = str(last_response.headers.get("retry-after") or "").strip()
            if retry_after.isdigit():
                cooldown_until = max(cooldown_until, time.time() + int(retry_after))
            if str(last_response.headers.get("x-ratelimit-remaining") or "").strip() == "0":
                reset_raw = str(last_response.headers.get("x-ratelimit-reset") or "").strip()
                if reset_raw.isdigit():
                    cooldown_until = max(cooldown_until, float(int(reset_raw)))
        _installation_repos_cooldown_until = cooldown_until
        _installation_repos_last_error = str(e)
        if _installation_repos_cache:
            return list(_installation_repos_cache)
        raise

    deduped = []
    seen = set()
    for repo in repos:
        name = str(repo or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        deduped.append(name)

    _installation_repos_cache = deduped
    _installation_repos_cached_at = time.time()
    _installation_repos_cooldown_until = 0.0
    _installation_repos_last_error = ""
    return list(_installation_repos_cache)


def _run(cmd, *, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _run_git_network(cmd, *, cwd=None, check=True):
    """
    Run a networked git command with ephemeral GitHub App auth when available.
    Uses env-based git config so secrets are not embedded in command args.
    """
    env = os.environ.copy()
    try:
        token = str(get_installation_token() or "").strip()
    except Exception:
        token = ""

    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        try:
            existing_count = int(str(env.get("GIT_CONFIG_COUNT", "0")).strip() or "0")
        except ValueError:
            existing_count = 0
        env[f"GIT_CONFIG_KEY_{existing_count}"] = "http.extraHeader"
        env[f"GIT_CONFIG_VALUE_{existing_count}"] = f"Authorization: Basic {basic}"
        env["GIT_CONFIG_COUNT"] = str(existing_count + 1)

    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True, env=env)


def ensure_repo_mirror(repo_name: str) -> str:
    owner = get_github_owner()
    if not owner:
        raise RuntimeError("GITHUB_OWNER is not configured")
    os.makedirs(REPOS_ROOT, exist_ok=True)
    repo_path = os.path.join(REPOS_ROOT, repo_name)

    if os.path.exists(repo_path):
        _run_git_network(["git", "fetch", "--prune"], cwd=repo_path)
    else:
        _run_git_network(
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
    Backward-compatible path used by earlier phases. Prefer prepare_job_worktree.
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

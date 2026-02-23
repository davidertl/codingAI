import requests

from github.app_auth import get_installation_token
from github.config import get_github_owner
API_BASE = "https://api.github.com"


def _headers():
    token = get_installation_token()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def get_pull_request(repo, pr_number):
    owner = get_github_owner()
    r = requests.get(
        f"{API_BASE}/repos/{owner}/{repo}/pulls/{int(pr_number)}",
        headers=_headers(),
    )
    r.raise_for_status()
    return r.json()


def get_pr_ci_status(repo, pr_number):
    pr = get_pull_request(repo, pr_number)
    owner = get_github_owner()
    head = pr.get("head", {}) if isinstance(pr, dict) else {}
    head_sha = str(head.get("sha", "")).strip()
    if not head_sha:
        raise RuntimeError(f"Missing head SHA for PR #{pr_number}")

    status_r = requests.get(
        f"{API_BASE}/repos/{owner}/{repo}/commits/{head_sha}/status",
        headers=_headers(),
    )
    status_r.raise_for_status()
    status_json = status_r.json()
    statuses = status_json.get("statuses", []) if isinstance(status_json, dict) else []

    checks_r = requests.get(
        f"{API_BASE}/repos/{owner}/{repo}/commits/{head_sha}/check-runs",
        headers=_headers(),
        params={"per_page": 100},
    )
    checks_r.raise_for_status()
    checks_json = checks_r.json()
    check_runs = checks_json.get("check_runs", []) if isinstance(checks_json, dict) else []

    status_pending = 0
    status_failure = 0
    status_success = 0
    for s in statuses:
        state = str(s.get("state", "")).lower()
        if state in {"pending"}:
            status_pending += 1
        elif state in {"failure", "error"}:
            status_failure += 1
        elif state in {"success"}:
            status_success += 1

    check_queued = 0
    check_in_progress = 0
    check_completed = 0
    conclusions = {}

    for c in check_runs:
        status = str(c.get("status", "")).lower()
        conclusion = str(c.get("conclusion", "")).lower()
        if status == "queued":
            check_queued += 1
        elif status in {"in_progress", "requested", "waiting", "pending"}:
            check_in_progress += 1
        elif status == "completed":
            check_completed += 1
            if conclusion:
                conclusions[conclusion] = int(conclusions.get(conclusion, 0)) + 1

    return {
        "repo": repo,
        "pr_number": int(pr_number),
        "pr_url": pr.get("html_url"),
        "head_sha": head_sha,
        "combined_state": str(status_json.get("state", "")).lower(),
        "status_context_total": len(statuses),
        "status_pending": status_pending,
        "status_failure": status_failure,
        "status_success": status_success,
        "check_total": len(check_runs),
        "check_queued": check_queued,
        "check_in_progress": check_in_progress,
        "check_completed": check_completed,
        "check_conclusions": conclusions,
    }

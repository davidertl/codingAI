import requests
from github.app_auth import get_installation_token
from github.config import get_github_owner


def _headers():
    token = get_installation_token()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def _without_pull_requests(items):
    out = []
    for item in items:
        # GitHub Issues API can include PRs; this agent only processes real issues.
        if isinstance(item, dict) and "pull_request" in item:
            continue
        out.append(item)
    return out


def get_ai_issues(repo):
    owner = get_github_owner()
    if not owner:
        raise RuntimeError("GITHUB_OWNER is not configured")
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    params = {"state": "open", "labels": "ai-fix", "per_page": 100}

    r = requests.get(url, headers=_headers(), params=params)
    r.raise_for_status()
    items = r.json()
    return _without_pull_requests(items if isinstance(items, list) else [])


def create_issue(repo, title, body):
    owner = get_github_owner()
    if not owner:
        raise RuntimeError("GITHUB_OWNER is not configured")
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    data = {"title": title, "body": body}

    r = requests.post(url, headers=_headers(), json=data)
    r.raise_for_status()
    return r.json()

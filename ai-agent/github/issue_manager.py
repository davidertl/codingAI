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


def get_ai_issues(repo, labels: str | None = None):
    owner = get_github_owner()
    if not owner:
        raise RuntimeError("GITHUB_OWNER is not configured")
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    label_str = str(labels or "").strip() or "ai-fix"
    params = {"state": "open", "labels": label_str, "per_page": 100}

    r = requests.get(url, headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    items = r.json()
    return _without_pull_requests(items if isinstance(items, list) else [])


def create_issue(repo, title, body):
    owner = get_github_owner()
    if not owner:
        raise RuntimeError("GITHUB_OWNER is not configured")
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    data = {"title": title, "body": body}

    r = requests.post(url, headers=_headers(), json=data, timeout=30)
    r.raise_for_status()
    return r.json()


def list_issue_comments(repo, issue_number, per_page=100):
    owner = get_github_owner()
    if not owner:
        raise RuntimeError("GITHUB_OWNER is not configured")
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
    r = requests.get(url, headers=_headers(), params={"per_page": per_page}, timeout=30)
    r.raise_for_status()
    return r.json()


def upsert_issue_comment(repo, issue_number, body, marker="<!-- codingai-failure-report -->"):
    owner = get_github_owner()
    if not owner:
        raise RuntimeError("GITHUB_OWNER is not configured")
    comments = list_issue_comments(repo, issue_number, per_page=100)

    existing_comment = None
    for comment in comments:
        if marker in (comment.get("body") or ""):
            existing_comment = comment
            break

    if existing_comment:
        comment_id = existing_comment["id"]
        update_url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{comment_id}"
        response = requests.patch(update_url, headers=_headers(), json={"body": body}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        return {
            "id": comment_id,
            "updated": True,
            "url": payload.get("html_url"),
            "marker": marker,
        }

    create_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
    response = requests.post(create_url, headers=_headers(), json={"body": body}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    return {
        "id": payload.get("id"),
        "updated": False,
        "url": payload.get("html_url"),
        "marker": marker,
    }

import requests
from github.app_auth import get_installation_token

OWNER = "davidertl"


def _headers():
    token = get_installation_token()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def _find_open_pr_for_branch(repo, branch):
    headers = _headers()
    check_url = f"https://api.github.com/repos/{OWNER}/{repo}/pulls"
    params = {
        "head": f"{OWNER}:{branch}",
        "state": "open",
    }
    r = requests.get(check_url, headers=headers, params=params)
    r.raise_for_status()
    existing = r.json()
    return existing[0] if existing else None


def get_open_pr_for_branch(repo, branch):
    pr = _find_open_pr_for_branch(repo, branch)
    if not pr:
        return None
    return {
        "number": pr["number"],
        "url": pr["html_url"],
    }


def create_or_get_pr(repo, branch, issue_number):
    pr = _find_open_pr_for_branch(repo, branch)
    if pr:
        print("PR already exists.")
        return {
            "number": pr["number"],
            "url": pr["html_url"],
        }

    # Create PR
    headers = _headers()
    create_url = f"https://api.github.com/repos/{OWNER}/{repo}/pulls"
    data = {
        "title": f"AI Fix for Issue #{issue_number}",
        "head": branch,
        "base": "main",
        "body": f"Automated fix attempt for issue #{issue_number}",
    }

    r = requests.post(create_url, headers=headers, json=data)
    if r.status_code != 201:
        print("PR creation failed:", r.text)
        return None

    pr = r.json()
    print("PR created.")
    return {
        "number": pr["number"],
        "url": pr["html_url"],
    }


def list_pr_comments(repo, pr_number, per_page=100):
    headers = _headers()
    list_url = f"https://api.github.com/repos/{OWNER}/{repo}/issues/{pr_number}/comments"
    r = requests.get(list_url, headers=headers, params={"per_page": per_page})
    r.raise_for_status()
    return r.json()


def has_ai_stop_comment(repo, pr_number, phrase="AI Stop"):
    phrase_lc = phrase.lower()
    for c in list_pr_comments(repo, pr_number):
        body = (c.get("body") or "").lower()
        if phrase_lc in body:
            return True, c
    return False, None


def upsert_pr_comment(repo, pr_number, body, marker="<!-- codingai-test-report -->"):
    headers = _headers()
    list_url = f"https://api.github.com/repos/{OWNER}/{repo}/issues/{pr_number}/comments"
    comments = list_pr_comments(repo, pr_number, per_page=100)

    existing_comment = None
    for c in comments:
        if marker in (c.get("body") or ""):
            existing_comment = c
            break

    if existing_comment:
        comment_id = existing_comment["id"]
        update_url = f"https://api.github.com/repos/{OWNER}/{repo}/issues/comments/{comment_id}"
        ur = requests.patch(update_url, headers=headers, json={"body": body})
        ur.raise_for_status()
        return {
            "id": comment_id,
            "updated": True,
            "url": ur.json().get("html_url"),
        }

    cr = requests.post(list_url, headers=headers, json={"body": body})
    cr.raise_for_status()
    created = cr.json()
    return {
        "id": created["id"],
        "updated": False,
        "url": created.get("html_url"),
    }

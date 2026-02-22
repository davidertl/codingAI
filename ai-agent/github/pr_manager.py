import requests
from github.app_auth import get_installation_token

OWNER = "davidertl"


def _headers():
    token = get_installation_token()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def create_or_get_pr(repo, branch, issue_number):
    headers = _headers()

    # Check whether a PR for the branch already exists
    check_url = f"https://api.github.com/repos/{OWNER}/{repo}/pulls"
    params = {
        "head": f"{OWNER}:{branch}",
        "state": "open",
    }

    r = requests.get(check_url, headers=headers, params=params)
    r.raise_for_status()

    existing = r.json()
    if existing:
        pr = existing[0]
        print("PR already exists.")
        return {
            "number": pr["number"],
            "url": pr["html_url"],
        }

    # Create PR
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


def upsert_pr_comment(repo, pr_number, body, marker="<!-- codingai-test-report -->"):
    headers = _headers()

    list_url = f"https://api.github.com/repos/{OWNER}/{repo}/issues/{pr_number}/comments"
    r = requests.get(list_url, headers=headers, params={"per_page": 100})
    r.raise_for_status()

    existing_comment = None
    for c in r.json():
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

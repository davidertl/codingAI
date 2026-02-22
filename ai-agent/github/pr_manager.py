import requests
from github.app_auth import get_installation_token

OWNER = "davidertl"


def create_or_get_pr(repo, branch, issue_number):
    token = get_installation_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }

    # 1️⃣ Prüfen ob bereits ein PR existiert
    check_url = f"https://api.github.com/repos/{OWNER}/{repo}/pulls"
    params = {
        "head": f"{OWNER}:{branch}",
        "state": "open"
    }

    r = requests.get(check_url, headers=headers, params=params)

    if r.status_code == 200 and len(r.json()) > 0:
        print("PR already exists.")
        return r.json()[0]["html_url"]

    # 2️⃣ PR erstellen
    create_url = f"https://api.github.com/repos/{OWNER}/{repo}/pulls"

    data = {
        "title": f"AI Fix for Issue #{issue_number}",
        "head": branch,
        "base": "main",
        "body": f"Automated fix attempt for issue #{issue_number}"
    }

    r = requests.post(create_url, headers=headers, json=data)

    if r.status_code == 201:
        print("PR created.")
        return r.json()["html_url"]
    else:
        print("PR creation failed:", r.text)
        return None